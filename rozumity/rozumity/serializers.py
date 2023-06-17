import re
from asyncio import ensure_future, iscoroutinefunction
from copy import deepcopy
from functools import wraps
from django.db.models.manager import BaseManager
from django.core.exceptions import ImproperlyConfigured, SynchronousOnlyOperation
from django.core.exceptions import ValidationError as DjangoValidationError
from asgiref.sync import sync_to_async
from rest_framework import serializers
from rest_framework.reverse import reverse
from rest_framework.exceptions import ValidationError
from rest_framework.utils.serializer_helpers import (
    BoundField, JSONBoundField, NestedBoundField, ReturnDict
)
from rest_framework.fields import (JSONField, Field, SkipField, get_error_detail)

reverse = sync_to_async(reverse)
deepcopy_async = sync_to_async(deepcopy)


async def get_field_info(obj):
    fields, forward_relations = {}, {}
    for field in obj.__class__._meta.fields:
        data = fields if not field.remote_field else forward_relations
        data[field.name] = {}
    return {'fields': fields, 'forward_relations': forward_relations}


class JSONAPISerializerRepr:
    def __init__(self, serializer, indent=1, force_many=None):
        self._serializer = serializer
        self._indent = indent
        self._force_many = force_many
    
    @staticmethod
    def _has_child(field):
        return hasattr(field, 'child')
    
    @staticmethod
    def _smart_repr(value):
        value = repr(value)
        if value.startswith("u'") and value.endswith("'"):
            return value[1:]
        return re.sub(' at 0x[0-9A-Fa-f]{4,32}>', '>', value)
    
    @classmethod
    def _field_repr(cls, field, force_many=False):
        kwargs = field._kwargs
        if force_many:
            kwargs = kwargs.copy()
            kwargs['many'] = True
            kwargs.pop('child', None)
        arg_string = ', '.join([cls._smart_repr(val) for val in field._args])
        kwarg_string = ', '.join([
            '%s=%s' % (key, cls._smart_repr(val))
            for key, val in sorted(kwargs.items())
        ])
        if arg_string and kwarg_string:
            arg_string += ', '
        if force_many:
            class_name = force_many.__class__.__name__
        else:
            class_name = field.__class__.__name__
        return "%s(%s%s)" % (class_name, arg_string, kwarg_string)
    
    #TODO: test case when list field contains numbers not serializers
    def __repr__(self):
        serializer, indent = self._serializer, self._indent
        ret = self._field_repr(serializer, self._force_many) + ':'
        indent_str = '    ' * indent
        if self._force_many:
            fields = self._force_many._declared_fields
        else:
            fields = serializer._declared_fields
        for field_name, field in fields.items():
            ret += '\n' + indent_str + field_name + ' = '
            required_string = '' if field.required else f'required={field.required}'
            if hasattr(field, '_declared_fields'):
                ret += self.__class__(field, indent + 1).__repr__().replace(
                    '()', f"({required_string})"
                )
            elif self._has_child(field):
                child = field.child
                if hasattr(child, '_declared_fields'):
                    ret += '{}({}child={})'.format(
                        field.__class__.__name__, required_string + ', ',
                        self.__class__(child, indent + 1).__repr__(),
                    )
                else:
                    ret += self._field_repr(child)
            elif hasattr(field, 'child_relation'):
                ret += self._field_repr(field.child_relation, force_many=field.child_relation)
            else:
                ret += self._field_repr(field)
        return ret


class NotSelectedForeignKey(ImproperlyConfigured):
    def __init__(self, message=None):
        self.message = (
            'Model.objects.select_related(<foreign_key_field_name>, ' 
            '<foreign_key_field_name>__<inner_foreign_key_field_name>) '
            'must be specified.'
        )
        super().__init__(self.message)


# TODO: write an JSONAPI object describing the server’s implementation (version)
class JSONAPIBaseSerializer:
    _creation_counter = 0
    source = None
    initial = None
    field_name = ''
    
    def __init__(self, instance=None, data=None, 
                 read_only=False, **kwargs):
        if data is not None:
            self.initial_data = data
        validators = list(kwargs.pop('validators', []))
        if validators:
            self.validators = validators
        self.instance = instance
        self.read_only = read_only
        self.initial = {}
        self.url_field_name = 'links'
        self._kwargs = kwargs
        self._args = {}
        self.partial = kwargs.pop('partial', False)
        self.required = kwargs.pop('required', True)
        self._context = kwargs.pop('context', {})
        self._view_name = kwargs.pop('view_name', None)
        request = self._context.get('request')
        if request:
            setattr(self, self.url_field_name, 
                    f'http://{request.get_host()}{request.path}')
        kwargs.pop('many', None)
        super().__init__(**kwargs)

    def __new__(cls, *args, **kwargs):
        Meta = getattr(cls, 'Meta', None)
        if Meta:
            parent_meta = cls.__bases__[0].Meta.__dict__
            for name, attr in parent_meta.items():
                if not hasattr(Meta, name):
                    setattr(Meta, name, attr)
        if kwargs.pop('many', False):
            return cls.many_init(*args, **kwargs)
        return super().__new__(cls)

    def __repr__(self):
        return str(JSONAPISerializerRepr(self))

    # Allow type checkers to make serializers generic.
    def __class_getitem__(cls, *args, **kwargs):
        return cls

    def __aiter__(self):
        self.iter_count = 0
        return self
    
    async def __anext__(self):
        fields = await self.fields
        try:
            key = list(fields.keys())[self.iter_count]
        except IndexError:
            raise StopAsyncIteration
        else:
            self.iter_count += 1
            return await self[key]
    
    async def __getitem__(self, key):
        fields = await self.fields
        field = fields[key]
        field.field_name = key
        if isinstance(field, JSONField):
            value = field.get_value(await self.__class__(self.instance).data)
            error = self._errors.get(key) if hasattr(self, '_errors') else None
            return JSONBoundField(field, value, error, key)
        elif isinstance(field, JSONAPIBaseSerializer):
            field = field.__class__(self.instance)
            data = await field.data
            data = {
                key: val for key, val in data.items() if key != 'included'
            }
            field.initial_data = data
            await field.is_valid()
            error = await field.errors
            return NestedBoundField(field, data, error, key)
        else:
            data = await self.__class__(self.instance).data
            value = data['data'].get(key)
            error = self._errors.get(key) if hasattr(self, '_errors') else None
            return BoundField(field, value, error, key)
    
    @classmethod
    def many_init(cls, *args, **kwargs):
        allow_empty = kwargs.pop('allow_empty', None)
        max_length = kwargs.pop('max_length', None)
        min_length = kwargs.pop('min_length', None)
        child_serializer = cls(*args, **kwargs)
        list_kwargs = {
            'child': child_serializer,
        }
        if allow_empty is not None:
            list_kwargs['allow_empty'] = allow_empty
        if max_length is not None:
            list_kwargs['max_length'] = max_length
        if min_length is not None:
            list_kwargs['min_length'] = min_length
        list_kwargs.update({
            key: value for key, value in kwargs.items()
            if key in serializers.LIST_SERIALIZER_KWARGS
        })
        meta = getattr(cls, 'Meta', None)
        list_serializer_class = getattr(meta, 'list_serializer_class', serializers.ListSerializer)
        return list_serializer_class(*args, **list_kwargs)
    
    def bind(self, field_name, parent):
        self.field_name = field_name
        self.parent = parent
    
    def validate(self, attrs):
        return attrs
    
    async def run_validators(self, value):
        errors = {}
        validators = await self.validators
        for field_name, validator in validators.items():
            subfield = field_name.split('.')
            if len(subfield) > 1 and field_name.startswith(subfield[0]):
                value_field = value.get(subfield[0]).get(subfield[-1])
            else:
                try:
                    value_field = value[field_name]
                except KeyError as e:
                    raise KeyError((
                        f"Serializer field named '{field_name}' was not not found. You need "
                        "to specify an 'attributes' or 'relationships' subfield."
                    ))
            try:
                if getattr(validator, 'requires_context', False):
                    validator(value_field, self)
                else:
                    validator(value_field)
            except ValidationError as exc:
                if isinstance(exc.detail, dict):
                    raise
                errors[field_name] = exc.detail
            except DjangoValidationError as exc:
                errors[field_name] = get_error_detail(exc)
            except TypeError as e:
                raise TypeError(
                    f"Wrong '{field_name}' field validator."
                ) from e
        if errors:
            raise ValidationError(errors)
    
    async def set_value(self, dictionary, keys, value):
        if not keys:
            dictionary.update(value)
            return
        for key in keys:
            if key not in dictionary:
                dictionary[key] = type(value)()
            if type(dictionary[key]) == list:
                dictionary[key].extend(value)
            else:
                dictionary[key] = value
    
    @property
    async def fields(self):
        return await self.get_fields()
    
    async def get_fields(self):
        return await deepcopy_async(self._declared_fields)
    
    async def get_initial(self):
        if callable(self.initial):
            return self.initial()
        return self.initial

    async def get_validators(self):
        meta = getattr(self, 'Meta', None)
        validators = getattr(meta, 'validators', None)
        return dict(validators) if validators else {}
    
    async def get_value(self, field_name, dictionary=None):
        return dictionary.get(field_name, None)

    async def run_validation(self, data={}):
        if data is not None:
            self.initial_data = data
        await self.is_valid(raise_exception=True)
        return await self.validated_data
    
    async def is_valid(self, *, raise_exception=False):
        if not hasattr(self, '_validated_data'):
            try:
                self._validated_data = await self.to_internal_value(self.initial_data)
            except ValidationError as exc:
                self._validated_data = {}
                self._errors = exc.detail
            else:
                self._errors = {}
        if self._errors and raise_exception:
            raise ValidationError(self._errors)
        return not bool(self._errors)
    
    @staticmethod
    async def _to_coroutine(function):
        if not iscoroutinefunction(function):
            function = sync_to_async(function)
        return function
    
    async def to_internal_value(self, data):
        meta = getattr(self, 'Meta', None)
        meta = getattr(meta, 'read_only_fields', [])
        ret = {}
        errors = {}
        fields = await self.fields
        for name, field in fields.items():
            if field.read_only or name in meta:
                continue
            if hasattr(field, 'child'):
                field.child.required, field = field.required, field.child
            value = await self.get_value(name, data)
            value = value.pop('data', value) if type(value) in [dict, list] else value
            value = [value] if type(value) != list else value
            run_validation = await self._to_coroutine(field.run_validation)
            validate_method = getattr(self, 'validate_' + name, None)
            for obj in value:
                errors_field = {}
                if hasattr(field, '_validated_data'):
                    del field._validated_data
                try:
                    validated_value = await run_validation(obj)
                    if validate_method is not None:
                        validate_method_awaited = await self._to_coroutine(validate_method)
                        validated_value = await validate_method_awaited(obj)
                except ValidationError as exc:
                    detail = exc.detail
                    if type(detail) == dict:
                        for key, val in detail.items():
                            errors_field[f'{name}.{key}'] = val
                    else:
                        errors_field[name] = detail
                except DjangoValidationError as exc:
                    errors_field[name] = get_error_detail(exc)
                except AttributeError as exc:
                    if field.required:
                        errors_field[name] = ValidationError(
                            'This field may not be null.'
                        ).detail
                except SkipField:
                    pass
                else:
                    if len(value) > 1:
                        validated_value = [validated_value]
                    await self.set_value(ret, [name], validated_value)
                errors.update(errors_field)
        if errors:
            raise ValidationError(errors)
        else:
            return ret
    
    async def to_representation(self, instance):
        fields = await self.fields
        instance_map = {key: getattr(instance, key) for key in fields.keys()}
        return {name: await self.get_value(name, instance_map) 
                for name in fields.keys()}

    @property
    async def _readable_fields(self):
        fields = await self.fields
        for field in fields.values():
            if not field.read_only:
                yield field
    
    @property
    async def validators(self):
        if not hasattr(self, '_validators'):
            self._validators = await self.get_validators()
        return self._validators
    
    @property
    async def data(self):
        if hasattr(self, 'initial_data') and not hasattr(self, '_validated_data'):
            msg = (
                'When a serializer is passed a `data` keyword argument you '
                'must call `.is_valid()` before attempting to access the '
                'serialized `.data` representation.\n'
                'You should either call `.is_valid()` first, '
                'or access `.initial_data` instead.'
            )
            raise AssertionError(msg)
        
        errors = getattr(self, '_errors', None)
        if errors:
            return errors

        if not hasattr(self, '_data'):
            if self.instance is not None:
                self._data = await self.to_representation(self.instance)
            elif hasattr(self, '_validated_data'):
                self._data = self._validated_data
            else:
                self._data = await self.get_initial()
        return ReturnDict(self._data, serializer=self)

    @property
    async def errors(self):
        if not hasattr(self, '_errors'):
            msg = 'You must call `.is_valid()` before accessing `.errors`.'
            raise AssertionError(msg)
        return self._errors
    
    @property
    async def validated_data(self):
        if not hasattr(self, '_validated_data'):
            msg = 'You must call `.is_valid()` before accessing `.validated_data`.'
            raise AssertionError(msg)
        return self._validated_data


class SerializerMetaclass(type):
    @classmethod
    def _get_declared_fields(cls, bases, attrs):
        obj_info = attrs.get('Type', None)
        if issubclass(obj_info.__class__, cls):
            attrs.update(obj_info._declared_fields)
        fields = {name: attrs.get(name, None) for name 
                  in ('Attributes', 'Relationships')}
        attrs.update({
            name.lower(): field()
            for name, field in fields.items()
            if field is not None
        })
        fields = [(field_name, attrs.pop(field_name))
                  for field_name, obj in list(attrs.items())
                  if isinstance(obj, Field) 
                  or isinstance(obj, JSONAPIBaseSerializer)]
        known = set(attrs)
        
        def visit(name):
            known.add(name)
            return name
        
        base_fields = [
            (visit(name), f)
            for base in bases if hasattr(base, '_declared_fields')
            for name, f in base._declared_fields.items() if name not in known
        ]
        return dict(base_fields + fields)

    def __new__(cls, name, bases, attrs):
        attrs['_declared_fields'] = cls._get_declared_fields(bases, attrs)
        return super().__new__(cls, name, bases, attrs)


class JSONAPITypeIdSerializer(JSONAPIBaseSerializer, metaclass=SerializerMetaclass):
    type = serializers.CharField()
    id = serializers.IntegerField()
    
    async def to_representation(self, instance):
        fields = await self.fields
        instance_map = {'type': instance.__class__.__name__.lower(), 
                        'id': instance.id}
        return {name: await self.get_value(name, instance_map) 
                for name in fields.keys()}


class JSONAPIAttributesSerializer(JSONAPIBaseSerializer, metaclass=SerializerMetaclass):
    pass


class JSONAPIRelationsSerializer(JSONAPIBaseSerializer, metaclass=SerializerMetaclass):
    async def to_representation(self, instance):
        fields = await self.fields
        data = {name: await self.get_value(
            name, {key: getattr(instance, key) for key in fields.keys()}
        ) for name in fields.keys()}
        url = getattr(self, self.url_field_name, None)
        value = []
        for key, val in data.items():
            if hasattr(val, 'all'):
                objects_list = [obj async for obj in val.all()]
            else:
                objects_list = [val] if val else []
            for obj in objects_list:
                data_included = {'type': obj.__class__.__name__.lower(), 'id': obj.id}
                value.append(data_included)
            data[key] = {'data': value.pop() if len(value) == 1 else value}
            if url:
                data[key][self.url_field_name] = {
                    'self': f"{url}relationships/{key}/",
                    'related': f"{url}{key}/"
                }
                field = fields[key]
                if hasattr(field, 'child'):
                    field, field._view_name = field.child, field.child._view_name
                if field._view_name:
                    data[key][self.url_field_name]['included'] = field._view_name
        return data


class JSONAPIManySerializer(JSONAPIBaseSerializer):
    child = None
    many = True
    
    def __init__(self, *args, **kwargs):
        self.child = kwargs.pop('child', deepcopy(self.child))
        self.allow_empty = kwargs.pop('allow_empty', True)
        self.max_length = kwargs.pop('max_length', None)
        self.min_length = kwargs.pop('min_length', None)
        assert self.child is not None, '`child` is a required argument.'
        super().__init__(*args, **kwargs)
        self.child.field_name, self.child.parent = '', self

    def __repr__(self):
        return str(JSONAPISerializerRepr(self, force_many=self.child))
    
    def __aiter__(self):
        self.iter_count = 0
        return self
    
    async def __anext__(self):
        fields = await self.child.fields
        try:
            key = list(fields.keys())[self.iter_count]
        except IndexError:
            raise StopAsyncIteration
        else:
            self.iter_count += 1
            return await self[key]
    
    async def __getitem__(self, key):
        fields = []
        async for obj in self.instance:
            data = self.child.__class__(obj)
            fields.append(await data[key])
        return fields
    
    async def to_internal_value(self, data):
        error_message = "The field must contain a valid object description."
        try:
            data = data['data']
        except KeyError:
            raise ValidationError({'data': [error_message]})
        else:
            if type(data) != list:
                raise ValidationError({'data': [
                    "Please provide a list of valid objects."
                    if data else error_message
                ]})
        validated_data = []
        for obj_data in data:
            obj_data = await self.child.run_validation({'data': obj_data})
            errors = self.child._errors
            if not errors:
                validated_data.append(await self.child.validated_data)
                del self.child._validated_data
            else:
                raise ValidationError(errors)
        self._validated_data = validated_data
        return validated_data
    
    async def to_representation(self, iterable):
        data = {'data': []}
        included = {}
        async for instance in iterable:
            obj_data = await self.child.__class__(
                instance, context={**self._context, 'is_included_disabled': True}
            ).data
            data['data'].append(obj_data['data'])
            await self.child._get_included(
                instance, obj_data.get('data').get('relationships'), 
                included, self._context.get('is_included_disabled', False)
            )
        if included:
            data['included'] = included.values()
            # Sort included
            # data['included'] = sorted(
            #    list(included.values()), 
            #    key=lambda x: (x['type'], x['id'])
            #)
        return data
    
    @property
    async def errors(self):
        return await self.child.__class__._format_errors(self)


# TODO: SERIALIZE A LIST OF INTEGERS IN THE ATTRIBUTES SECTION
class JSONAPISerializer(JSONAPIBaseSerializer, metaclass=SerializerMetaclass):
    class Type(JSONAPITypeIdSerializer):
        pass
    
    class Attributes(JSONAPIAttributesSerializer):
        pass
    
    class Relationships(JSONAPIRelationsSerializer):
        pass
    
    class Meta:
        list_serializer_class = JSONAPIManySerializer
        read_only_fields = ('id')
    
    @property
    async def errors(self):
        return await self._format_errors()
    
    async def _format_errors(self):
        if not hasattr(self, '_errors'):
            msg = 'You must call `.is_valid()` before accessing `.errors`.'
            raise AssertionError(msg)
        errors = {}
        for key, val in self._errors.items():
            if type(val) == dict:
                errors.update(val)
            else:
                errors[key] = val
        if not errors:
            return None
        error_details = []
        for key, val in errors.items():
            error_detail = {'code': 403}
            url = getattr(self, self.url_field_name, None)
            if url:
                error_detail['source'] = {'pointer': url}
            error_detail['detail'] = (f"The JSON field '{key}' caused an "
                                      f"exception: {val[0].lower()}")
            error_details.append(error_detail)
        return {"jsonapi": { "version": "1.1" }, 'errors': error_details}
    
    async def _get_included(self, instance, rels, included, 
                            is_included_disabled=False):
        if not rels or is_included_disabled:
            return
        for rel in rels.keys():
            rel_field = getattr(instance, rel)
            view_name = rels[rel]['links'].pop('included')
            if hasattr(rel_field, 'all'):
                objects_list = [obj async for obj in rel_field.all()]
            else:
                objects_list = [rel_field]
            field_info = await get_field_info(objects_list[0])
            for obj in objects_list:
                data_included = {'type': obj.__class__.__name__.lower(), 'id': obj.id}
                key = "_".join(str(data_included.values()))
                if included.get(key):
                    continue
                for attribute in field_info.get('fields').keys():
                    if not data_included.get('attributes'):
                        data_included['attributes'] = {}
                    data_included['attributes'][attribute] = getattr(obj, attribute)
                for relationship in field_info.get('forward_relations').keys():
                    if not data_included.get('relationships'):
                        data_included['relationships'] = {}
                    objects_list = getattr(obj, relationship)
                    try:
                        objects_list = [obj async for obj in objects_list.all()]
                    except (AttributeError, TypeError):
                        objects_list = [objects_list]
                    if not objects_list:
                        continue
                    objects_list = [await JSONAPITypeIdSerializer(obj).data 
                                    for obj in objects_list]
                    data_included['relationships'][relationship] = {
                        'data': objects_list if len(objects_list) > 1 else objects_list.pop()
                    }
                data_included['links'] = {'self': await reverse(
                    view_name, args=[data_included['id']],
                    request=self._context.get('request')
                )}
                included[key] = data_included
    
    async def to_internal_value(self, data):
        error_message = "The field must contain a valid object description."
        try:
            data = data['data']
            data['type']
        except KeyError:
            raise ValidationError({'data': [error_message]})
        except TypeError:
            raise ValidationError({'data': ["A list of objects is not supported."]})
        await self.run_validators(data)
        internal_value = await JSONAPIBaseSerializer.to_internal_value(self, data)
        return {**internal_value.get('attributes', {}),
                'relationships': internal_value.get('relationships', {})}
    
    async def to_representation(self, instance):
        fields = await self.fields
        obj_map = {**await self.Type(instance).data}
        serializer_map = {
            'attributes': fields['attributes'].__class__(instance),
            'relationships': fields['relationships'].__class__(
                instance, context={**self._context}
            )
        }
        url = getattr(self, self.url_field_name, None)
        parent_id = str(obj_map['id'])
        if url and not url.endswith(parent_id + '/'):
            url = f"{url}{parent_id}/"
        setattr(serializer_map['relationships'], self.url_field_name, url)
        for key, val in serializer_map.items():
            if len(val._declared_fields):
                try:
                    obj_map[key] = await val.data
                except SynchronousOnlyOperation as e:
                    raise NotSelectedForeignKey from e
            else:
                obj_map[key] = {}
        data = {name: await self.get_value(name, obj_map) for name in 
                fields.keys() if name in obj_map and name != 'included'}
        included = {}
        if url:
            await self._get_included(
                instance, data.get('relationships'), included,
                self._context.get('is_included_disabled', False)
            )
            data['links'] = {'self': url}
        data = {'data': data}
        if included:
            data['included'] = list(included.values())
        return data

    async def validate_type(self, value):
        obj_type = getattr(self.Meta, 'model_type', None)
        if obj_type is None:
            obj_type = getattr(self.Meta, 'model', '')
            obj_type = obj_type.__name__.lower() if obj_type else ''
            value = ''.join(value.split('_'))
        if not value or value != obj_type:
            raise serializers.ValidationError("Incorrect object type.")
        return value
