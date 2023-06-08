import re
import inspect
from asyncio import ensure_future, iscoroutinefunction
from copy import deepcopy
from functools import wraps
from django.db.models.manager import BaseManager
from django.core.exceptions import ImproperlyConfigured, SynchronousOnlyOperation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import MaxLengthValidator, MinLengthValidator
from django.contrib.sites.shortcuts import get_current_site
from rest_framework import serializers
from rest_framework.reverse import reverse
from rest_framework.exceptions import ValidationError
from rest_framework.utils.serializer_helpers import (
    BindingDict, BoundField, JSONBoundField, NestedBoundField, ReturnDict
)
from rest_framework.utils.formatting import lazy_format
from rest_framework.fields import (JSONField, CharField, IntegerField, 
                                   Field, SkipField, get_error_detail)
from asgiref.sync import sync_to_async

reverse = sync_to_async(reverse)
deepcopy_async = sync_to_async(deepcopy)


async def get_field_info(model):
    opts = model._meta.concrete_model._meta
    fields, forward_relations = {}, {}
    for field in (field for field in opts.fields if field.serialize and not field.remote_field):
        fields[field.name] = {}
    for field in (field for field in opts.fields if field.serialize and field.remote_field):
        forward_relations[field.name] = {'to_many': False}
    # Deal with forward many-to-many relationships.
    for field in (field for field in opts.many_to_many if field.serialize):
        forward_relations[field.name] = {'to_many': True}
    return {'fields': fields, 'forward_relations': forward_relations}


class cached_property(object):
    """
    A property that is only computed once per instance and then replaces itself
    with an ordinary attribute. Deleting the attribute resets the property.
    Source: https://github.com/bottlepy/bottle/commit/fa7733e075da0d790d809aa3d2f53071897e6f76
    """

    def __init__(self, func):
        self.__doc__ = getattr(func, "__doc__")
        self.func = func

    def __get__(self, obj, cls):
        if obj is None:
            return self
        if iscoroutinefunction(self.func):
            return self._wrap_in_coroutine(obj)
        value = obj.__dict__[self.func.__name__] = self.func(obj)
        return value

    def _wrap_in_coroutine(self, obj):
        @wraps(obj)
        async def wrapper():
            future = ensure_future(self.func(obj))
            obj.__dict__[self.func.__name__] = future
            return await future
        return wrapper()


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


class ListField(serializers.ListField):
    def __init__(self, **kwargs):
        self.child = kwargs.pop('child', deepcopy(self.child))
        self.allow_empty = kwargs.pop('allow_empty', True)
        self.max_length = kwargs.pop('max_length', None)
        self.min_length = kwargs.pop('min_length', None)

        assert not inspect.isclass(self.child), '`child` has not been instantiated.'
        assert self.child.source is None, (
            "The `source` argument is not meaningful when applied to a `child=` field. "
            "Remove `source=` from the field declaration."
        )

        serializers.Field.__init__(self, **kwargs)
        self.child.field_name = ''
        self.child.parent = self
        if self.max_length is not None:
            message = lazy_format(self.error_messages['max_length'], max_length=self.max_length)
            self.validators.append(MaxLengthValidator(self.max_length, message=message))
        if self.min_length is not None:
            message = lazy_format(self.error_messages['min_length'], min_length=self.min_length)
            self.validators.append(MinLengthValidator(self.min_length, message=message))


# TODO: create length validation, test type validation, probably remove type validation
# TODO: create validation methods for serializers and fields
# TODO: check if prefetch_related works with async queryset
# TODO: make to_internal_value base method to reuse it in all the serializers
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
        self._kwargs = kwargs
        self._args = {}
        self.required = kwargs.pop('required', True)
        self.partial = kwargs.pop('partial', False)
        self._context = kwargs.pop('context', {})
        self._view_name = kwargs.pop('view_name', None)
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

        for key in keys[:-1]:
            if key not in dictionary:
                dictionary[key] = {}
            dictionary = dictionary[key]

        dictionary[keys[-1]] = value
    
    @cached_property
    async def fields(self):
        fields = BindingDict(self)
        declared_fields = await self.get_fields()
        for key, value in declared_fields.items():
            fields[key] = value
        return dict(fields)
    
    async def get_fields(self):
        return await deepcopy_async(self._declared_fields)
    
    async def get_initial(self):
        if callable(self.initial):
            return self.initial()
        return self.initial
    
    async def get_value(self, field_name, dictionary=None):
        return dictionary.get(field_name, None)

    async def run_validation(self, data={}):
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
    
    async def to_internal_value(self, data):
        ret = {}
        errors = {}
        fields = await self.fields
        for name, field in fields.items():
            value = await self.get_value(name, data)
            try:
                validated_value = field.run_validation(value)
                try:
                    validated_value = await validated_value
                except TypeError:
                    pass
            except ValidationError as exc:
                detail = exc.detail
                if not field.required and not value:
                    pass
                else:
                    errors[
                        f'attributes.{field.field_name}' if 
                        self.__class__.__name__.lower() == 'attributes' 
                        else name
                    ] = detail
            except AttributeError as exc:
                if field.required:
                    errors[f'attributes.{field.field_name}'] = ValidationError(
                        'This field may not be null.'
                    ).detail
            except DjangoValidationError as exc:
                errors[name] = get_error_detail(exc)
            except SkipField:
                pass
            else:
                await self.set_value(ret, {}, {name: validated_value})
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
        return self._data

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
    required_fields = {
        'Attributes': True, 'Relationships': True, 'Included': False
    }
    
    @classmethod
    def _get_declared_fields(cls, bases, attrs):
        obj_info = attrs.get('Type', None)
        if issubclass(obj_info.__class__, cls):
            attrs.update(obj_info._declared_fields)
        required_fields = cls.required_fields
        fields = {name: attrs.get(name, None) for name in required_fields}
        attrs.update({
            name.lower(): field(required=required_fields[name])
            for name, field in fields.items() 
            if issubclass(field.__class__, cls)
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
        iterable = self.instance.all() if isinstance(self.instance, BaseManager) else self.instance
        fields = []
        for obj in iterable:
            field = self.child.__class__(obj)
            fields.append(await field[key])
        return fields
    
    async def to_representation(self, data):
        self.iterable = await sync_to_async(data.all)() if isinstance(data, BaseManager) else data
        data = {'data': []}
        included = {}
        for obj in self.iterable:
            obj_data = await self.child.__class__(
                obj, context={**self._context, 'many': True}
            ).data
            included_obj_data = obj_data.pop('included', [])
            if included_obj_data is not None:
                for obj in included_obj_data:
                    key = f'{obj["type"]}_{obj["id"]}'
                    try:
                        included[key]
                    except KeyError:
                        included[key] = obj
            data['data'].append(*obj_data.pop('data'))
        if included:
            data['included'] = list(included.values())
            if self.child.source:
                for obj_data in included.values():
                    obj_data['links'] = {'self': await reverse(
                        *obj_data['links'], 
                        request=self._context.get('request')
                    )}
            # Sort included
            # data['included'] = sorted(
            #    list(included.values()), 
            #    key=lambda x: (x['type'], x['id'])
            #)
        return data


class JSONAPITypeIdSerializer(JSONAPIBaseSerializer, metaclass=SerializerMetaclass):
    type = CharField(validators=[ValidateFieldType(str)])
    id = IntegerField(validators=[ValidateFieldType(int)])
    
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
    async def _add_value(self, dictionary, keys, value):
        for key in keys:
            if not dictionary.get(key):
                dictionary[key] = {'data': []}
            dictionary[key]['data'].append(value)
    
    async def to_internal_value(self, data):
        ret = {}
        errors = {}
        fields = await self.fields
        for name, field in fields.items():
            value = await self.get_value(name, data)
            value = value.pop('data', value) if type(value) == dict else value
            value = [value] if type(value) != list else value
            error_name = f'relationships.{field.field_name}.data'
            if hasattr(field, 'child'):
                field.child.required, field = field.required, field.child
            for obj in value:
                try:
                    validated_value = field.__class__(data=obj).run_validation(obj)
                    try:
                        validated_value = await validated_value
                    except TypeError:
                        pass
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
                    if field.required:
                        errors[error_name] = ValidationError(
                            'This field may not be null.'
                        ).detail
                except SkipField:
                    pass
                else:
                    if len(value) == 1:
                        await self.set_value(
                            ret, {}, {name: {'data': validated_value}}
                        )
                    else:
                        await self._add_value(ret, [name], validated_value)
        if errors:
            raise ValidationError(errors)
        else:
            return ret
    
    async def to_representation(self, instance):
        fields = await self.fields
        data = {name: await self.get_value(
            name, {key: getattr(instance, key) for key in fields.keys()}
        ) for name in fields.keys()}
        parent_id = str(self._context.get("parent_id"))
        source = self.source
        if source is not None:
            source_pk = source.split('/')[-2]
            if source_pk != parent_id:
                source = source.replace(source_pk, parent_id)
            else:
                source += parent_id
        value, included = [], []
        for key, val in data.items():
            if hasattr(val, 'all'):
                objects_list = [e async for e in val.all()]
            else:
                objects_list = [val]
            field_info = await get_field_info(objects_list[0])
            for obj in objects_list:
                data_included = {'type': obj.__class__.__name__.lower(), 'id': obj.id}
                data_new = data_included.copy()
                value.append(data_new)
                for attribute in field_info.get('fields').keys():
                    if not data_included.get('attributes'):
                        data_included['attributes'] = {}
                    data_included['attributes'][attribute] = getattr(obj, attribute)
                for relationship in field_info.get('forward_relations').keys():
                    if not data_included.get('relationships'):
                        data_included['relationships'] = {}
                    objects_list = getattr(obj, relationship)
                    # TODO: to test an included relation's many to many
                    try:
                        objects_list = await objects_list.all()
                    except AttributeError:
                        objects_list = [objects_list]
                    if not objects_list:
                        continue
                    objects_list = [await JSONAPITypeIdSerializer(inner_obj).data 
                                    for inner_obj in objects_list]
                    data_included['relationships'][relationship] = {
                        'data': objects_list if len(objects_list) > 1 else objects_list.pop()
                    }
                field = fields[key]
                if hasattr(field, 'child'):
                    field, field._view_name = field.child, field.child._view_name
                if field._view_name and self.source:
                    data_included['links'] = [field._view_name, [data_included['id']]]
                included.append(data_included)
            data[key] = {'data': value.pop() if len(value) == 1 else value}
            if self.source:
                data[key]['links'] = {
                    'self': f"{source}relationships/{key}/",
                    'related': f"{source}{key}/"
                }
        data['included'] = included
        return data

# TODO: add _view_name to included as a separate field, execute reverse in the ManySerializer and to_representation
class JSONAPISerializer(JSONAPIBaseSerializer, metaclass=SerializerMetaclass):
    class Type(JSONAPITypeIdSerializer):
        pass
    
    class Attributes(JSONAPIAttributesSerializer):
        pass
    
    class Relationships(JSONAPIRelationsSerializer):
        pass
    
    class Included(JSONField):
        pass
    
    class Meta:
        list_serializer_class = JSONAPIManySerializer
    
    def __init__(self, instance=None, data=None, **kwargs):
        super().__init__(instance, data, **kwargs)
        context = self._context
        request, source = context.get('request'), context.get('source')
        if source:
            self.source = source
        elif request:
            self.source = f'http://{get_current_site(self.request)}{self.request.path}'
    
    async def to_internal_value(self, data):
        ret = {}
        errors = {}
        fields = await self.fields
        for name, field in fields.items():
            if name == 'included':
                continue
            value = await self.get_value(name, data)
            if isinstance(field, JSONAPIBaseSerializer):
                field = field.__class__(
                    data={} if value is None else value
                )
            else:
                pass
            try:
                validated_value = field.run_validation(value)
                try:
                    validated_value = await validated_value
                except TypeError:
                    pass
            except ValidationError as exc:
                errors[name] = exc.detail
            except DjangoValidationError as exc:
                errors[name] = get_error_detail(exc)
            except SkipField:
                pass
            else:
                await self.set_value(ret, {}, {name: validated_value})
        if errors:
            raise ValidationError(errors)
        else:
            return ret
    
    async def to_representation(self, instance):
        fields = await self.fields
        obj_map = {**await self.Type(instance).data}
        serializer_map = {
            'attributes': fields['attributes'].__class__(instance),
            'relationships': fields['relationships'].__class__(
                instance, context={**self._context, 'parent_id': obj_map['id']}
            )
        }
        source = self.source
        serializer_map['relationships'].source = source
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
        included = data['relationships'].pop('included', [])
        if self.source and not self._context.get('many', None):
            for data in included:
                data['links']= {'self': await reverse(
                    *data['links'], request=self._context.get('request')
                )}
        return {
            'data': [{**data, 'links': {'self': source}}], 
            'links': {'self': source}, 'included': included
        } if source else {'data': [data], 'included': included}

    @cached_property
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
            if self.source:
                error_detail['source'] = {'pointer': self.source}
            error_detail['detail'] = f"The JSON field '{key}' caused an exception: {val.pop().lower()}"
            error_details.append(error_detail)
        return {"jsonapi": { "version": "1.1" }, 'errors': error_details}
    