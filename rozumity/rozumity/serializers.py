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
    def _has_declared_fields(field):
        return hasattr(field, '_declared_fields')
    
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
            if self._has_declared_fields(field):
                ret += self.__class__(field, indent + 1).__repr__().replace(
                    '()', f"({required_string})"
                )
            elif self._has_child(field):
                child = field.child
                if self._has_declared_fields(child):
                    ret += '{}({}child={}'.format(
                        field.__class__.__name__, required_string + ', ',
                        self.__class__(child, indent + 1).__repr__().replace('()', '())'),
                    )
                else:
                    ret += self._field_repr(child)
            elif hasattr(field, 'child_relation'):
                ret += self._field_repr(field.child_relation, force_many=field.child_relation)
            else:
                ret += self._field_repr(field)
        if getattr(serializer, 'validators', None):
            ret += '\n' + indent_str + 'class Meta:'
            ret += '\n' + indent_str + '    validators = ' + self._smart_repr(serializer.validators)
        return ret


class NotSelectedForeignKey(ImproperlyConfigured):
    def __init__(self, message=None):
        self.message = (
            'Model.objects.select_related(<foreign_key_field_name>, ' 
            '<foreign_key_field_name>__<inner_foreign_key_field_name>) '
            'must be specified.'
        )
        super().__init__(self.message)


class ValidateFieldType:
    def __init__(self, python_type):
        self.type = python_type

    def __call__(self, value):
        if type(value) != self.type:
            message = f"The value {value} is wrong type."
            raise serializers.ValidationError(message)


# TODO: create validation methods for serializers
# TODO: make to_internal_value base method to reuse it in all the serializers
# TODO: write an JSONAPI object describing the server’s implementation (version)
class JSONAPIBaseSerializer:
    _creation_counter = 0
    source = None
    initial = None
    field_name = ''
    
    def __init__(self, instance=None, data=None, **kwargs):
        self.instance = instance
        if data is not None:
            self.initial_data = data
        self.initial = {}
        self.url_field_name = 'links'
        self._kwargs = kwargs
        self._args = {}
        self.partial = kwargs.pop('partial', False)
        self.required = kwargs.pop('required', True)
        #setattr(self, self.url_field_name, 
        #        kwargs.pop(self.url_field_name, None))
        self._context = kwargs.pop('context', {})
        self._view_name = kwargs.pop('view_name', None)
        request = self._context.get('request')
        if request:
            setattr(self, self.url_field_name, 
                    f'http://{request.get_host()}{request.path}')
        kwargs.pop('many', None)
        super().__init__(**kwargs)

    def __new__(cls, *args, **kwargs):
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
            value = data['data'][0].get(key)
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
    
    async def set_value(self, dictionary, keys, value):
        if not keys:
            dictionary.update(value)
            return
        for key in keys:
            if key not in dictionary:
                dictionary[key] = {}
            if len(value) > 1:
                if dictionary[key].get('data') is None:
                    dictionary[key]['data'] = []
                dictionary[key]['data'].append(value)
            else:
                dictionary[key] = {'data': value}
    
    @property
    async def fields(self):
        return await self.get_fields()
    
    async def get_fields(self):
        return await deepcopy_async(self._declared_fields)
    
    async def get_initial(self):
        if callable(self.initial):
            return self.initial()
        return self.initial
    
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
    
    async def _included_helper(self, included, objects_list, view_name):
        field_info = await get_field_info(objects_list[0])
        for obj in objects_list:
            data_included = {'type': obj.__class__.__name__.lower(), 'id': obj.id}
            key = f"{data_included['type']}_{data_included['id']}"
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

    async def _to_internal_value_helper(self, validation_coroutine, error_name, required):
        validated_value, errors = {}, {}
        try:
            validated_value = await validation_coroutine
        except ValidationError as exc:
            detail = exc.detail
            if type(detail) == dict:
                for key, val in detail.items():
                    errors[f'{error_name}.{key}'] = val
            else:
                errors[error_name] = detail
        except DjangoValidationError as exc:
            errors[error_name] = get_error_detail(exc)
        except AttributeError as exc:
            if required:
                errors[error_name] = ValidationError(
                    'This field may not be null.'
                ).detail
        except SkipField:
            pass
        return validated_value, errors
    
    @staticmethod
    async def _to_coroutine(function):
        if not iscoroutinefunction(function):
            function = sync_to_async(function)
        return function
    
    async def to_internal_value(self, data):
        ret = {}
        errors = {}
        fields = await self.fields
        for name, field in fields.items():
            run_validation = await self._to_coroutine(field.run_validation)
            
            validated_value, errors_field = await self._to_internal_value_helper(
                run_validation(await self.get_value(name, data)), 
                name, field.required
            )
            if not errors_field:
                await self.set_value(ret, {}, {name: validated_value})
            else:
                errors.update(errors_field)
        if errors:
            raise ValidationError(errors)
        else:
            return ret
    
    async def to_representation(self, instance):
        raise NotImplemented(
            'Method JSONAPIBaseSerializer.to_representation'
            '(self, instance) is not implemented'
        )
    
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
    
    async def to_representation(self, iterable):
        data = {'data': []}
        included = {}
        async for obj in iterable:
            obj_data = await self.child.__class__(
                obj, context={**self._context, 'is_included_disabled': True}
            ).data
            data['data'].append(*obj_data['data'])
            rels = obj_data.get('data')[0].get('relationships')
            if rels:
                for rel in rels.keys():
                    rel_field = getattr(obj, rel)
                    view_name = rels[rel]['links'].pop('included')
                    if hasattr(rel_field, 'all'):
                        objects_list = [obj async for obj in rel_field.all()]
                    else:
                        objects_list = [rel_field]
                    await self._included_helper(included, objects_list, view_name)
                    
        if included:
            data['included'] = included.values()
            # Sort included
            # data['included'] = sorted(
            #    list(included.values()), 
            #    key=lambda x: (x['type'], x['id'])
            #)
        return data


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
    async def to_representation(self, instance):
        fields = await self.fields
        instance_map = {key: getattr(instance, key) for key in fields.keys()}
        return {name: await self.get_value(name, instance_map) 
                for name in fields.keys()}


class JSONAPIRelationsSerializer(JSONAPIBaseSerializer, metaclass=SerializerMetaclass):
    # TODO: REWRITE WITH BASE CLASS METHOD
    async def to_internal_value(self, data):
        ret = {}
        errors = {}
        fields = await self.fields
        for name, field in fields.items():
            if hasattr(field, 'child'):
                field.child.required, field = field.required, field.child
            value = await self.get_value(name, data)
            value = value.pop('data', value) if type(value) == dict else value
            value = [value] if type(value) != list else value
            run_validation = await self._to_coroutine(field.run_validation)
            for obj in value:
                validated_value, errors_field = await self._to_internal_value_helper(
                    run_validation(obj), f'{name}.data', field.required
                )
                if not errors_field:
                    await self.set_value(ret, {name: None}, validated_value)
                else:
                    errors.update(errors_field)
        if errors:
            raise ValidationError(errors)
        else:
            return ret
    
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
                objects_list = [val]
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
    
    async def to_internal_value(self, data):
        error_message = "The field must contain a valid object description."
        try:
            data = data['data']
        except KeyError:
            raise ValidationError({'data': [error_message]})
        if type(data) == list:
            if len(data) == 1:
                data = data[0]
            else:
                raise ValidationError({'data': [
                    "The bulk action is not supported." 
                    if data else error_message
                ]})
        return await JSONAPIBaseSerializer.to_internal_value(self, data)
    
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
        # TODO: REWRITE LIKE MANY SERIALIZER
        included_all = []
        rels = data.get('relationships')
        if url and not self._context.get('is_included_disabled', False):
            included = {}
            for rel in rels.keys():
                rel_field = getattr(instance, rel)
                view_name = rels[rel]['links'].pop('included')
                if hasattr(rel_field, 'all'):
                    objects_list = [obj async for obj in rel_field.all()]
                else:
                    objects_list = [rel_field]
                await self._included_helper(included, objects_list, view_name)
            included_all = included.values()
        return {'data': [{**data, 'links': {'self': url}}] 
                if url else [{'data': [data]}], 'included': included_all}

    @property
    async def errors(self):
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
