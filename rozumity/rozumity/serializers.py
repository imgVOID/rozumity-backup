import inspect
from copy import deepcopy
from django.db.models.manager import BaseManager
from django.core.exceptions import ImproperlyConfigured, SynchronousOnlyOperation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import MaxLengthValidator, MinLengthValidator
from django.apps import apps
from rest_framework import serializers
from django.utils.functional import cached_property
from rest_framework.settings import api_settings
from rest_framework.utils.model_meta import get_field_info
from rest_framework.exceptions import ValidationError
from rest_framework.relations import Hyperlink, PKOnlyObject
from rest_framework.utils.serializer_helpers import (BindingDict, ReturnDict)
from rest_framework.utils.formatting import lazy_format

from rest_framework.fields import (JSONField, CharField, IntegerField, 
                                   Field, SkipField, get_error_detail)
from asgiref.sync import sync_to_async

get_field_info = sync_to_async(get_field_info)


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


class BindingDict(BindingDict):
    def __setitem__(self, key, field):
        self.fields[key] = field
        self.field_name = key
        self.parent = self.serializer


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


# TODO: write JSONAPI urls
# TODO: test of types and length validation
# TODO: check if prefetch_related works with async queryset
# TODO: make to_internal_value base method to reuse it in all the serializers
class JSONAPIBaseSerializer:
    _creation_counter = 0
    source = None
    initial = None
    
    def __init__(self, instance=None, data=None, **kwargs):
        self.instance = instance
        if data is not None:
            self.initial_data = data
        self.initial = data
        self.required = kwargs.pop('required', True)
        self.partial = kwargs.pop('partial', False)
        self._context = kwargs.pop('context', {})
        if self._context.get('request'):
            self.full_path = self._context.get('request').get_full_path()
        kwargs.pop('many', None)
        super().__init__(**kwargs)

    def __new__(cls, *args, **kwargs):
        if kwargs.pop('many', False):
            return cls.many_init(*args, **kwargs)
        return super().__new__(cls)

    # Allow type checkers to make serializers generic.
    def __class_getitem__(cls, *args, **kwargs):
        return cls

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
    
    @cached_property
    def fields(self):
        fields = BindingDict(self)
        for key, value in self.get_fields().items():
            fields[key] = value
        return fields

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
                self._data = self.get_initial()
        return self._data
    
    def validate(self, attrs):
        return attrs
    
    @property
    async def validated_data(self):
        if not hasattr(self, '_validated_data'):
            msg = 'You must call `.is_valid()` before accessing `.validated_data`.'
            raise AssertionError(msg)
        return self._validated_data

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
        errors = {"jsonapi": { "version": "1.1" }, 'errors': [{
            'code': 403, 'source': {'pointer': self.full_path}, 
            'title': f"The '{key}' field caused an exception: {val.pop().lower()}"
        } for key, val in errors.items()]}
        return errors
    
    async def get_initial(self):
        if callable(self.initial):
            return self.initial()
        return self.initial
    
    def get_fields(self):
        return deepcopy(self._declared_fields)
    
    def bind(self, field_name, parent):
        self.field_name = field_name
        self.parent = parent
    
    async def set_value(self, dictionary, keys, value):
        if not keys:
            dictionary.update(value)
            return

        for key in keys[:-1]:
            if key not in dictionary:
                dictionary[key] = {}
            dictionary = dictionary[key]

        dictionary[keys[-1]] = value
    
    async def is_valid(self, *, raise_exception=False):
        if not hasattr(self, '_validated_data'):
            try:
                self._validated_data = await self.to_internal_value(self.initial_data)
            except ValidationError as exc:
                print(exc.detail)
                self._validated_data = {}
                self._errors = exc.detail
            else:
                self._errors = {}
        if self._errors and raise_exception:
            raise ValidationError(self._errors)
        return not bool(self._errors)
    
    async def run_validation(self, data={}):
        await self.is_valid(raise_exception=True)
        return await self.validated_data
    
    async def _raise_if_required_empty(self, name, field, value):
        if not value and field.required:
            raise ValidationError(
                f"The '{name}' attribute field is required."
            )

    async def to_internal_value(self, data):
        ret = {}
        errors = {}
        fields = self.fields
        for name, field in fields.items():
            print(name, data, self)
            value = await self.get_value(name, data)
            try:
                validated_value = field.run_validation(value)
                try:
                    validated_value = await validated_value
                except TypeError:
                    pass
            except ValidationError as exc:
                errors[f'attributes.{name}'] = ValidationError(
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
    
    async def get_value(self, field_name, dictionary=None):
        value = dictionary.get(field_name, None)
        return value.pop('data', value) if type(value) == dict else value
    
    async def to_representation(self, instance):
        raise NotImplemented(
            'Method JSONAPIBaseSerializer.to_representation'
            '(self, instance) is not implemented'
        )


class SerializerMetaclass(type):
    @classmethod
    def _get_declared_fields(cls, bases, attrs):
        obj_info = attrs.get('Type', None)
        attributes = attrs.get('Attributes', None)
        relationships = attrs.get('Relationships', None)
        if issubclass(obj_info.__class__, cls):
            attrs.update(obj_info._declared_fields)
        if attributes:
            attrs['attributes'] = attributes()
        if relationships:
            attrs['relationships'] = relationships()
        
        fields = [(field_name, attrs.pop(field_name))
                  for field_name, obj in list(attrs.items())
                  if isinstance(obj, Field) or isinstance(obj, JSONAPIBaseSerializer)]

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
    type = CharField(validators=[ValidateFieldType(str)])
    id = IntegerField(validators=[ValidateFieldType(int)])
    
    async def to_representation(self, instance):
        fields = self.fields
        instance_map = {'type': instance.__class__.__name__.lower(), 
                        'id': instance.id}
        return {name: await self.get_value(name, instance_map) for name in fields.keys()}


class JSONAPIAttributesSerializer(JSONAPIBaseSerializer, metaclass=SerializerMetaclass):
    async def to_representation(self, instance):
        fields = self.fields
        instance_map = {key: getattr(instance, key) for key in fields.keys()}
        return {name: await self.get_value(name, instance_map) for name in fields.keys()}


class JSONAPIRelationsSerializer(JSONAPIBaseSerializer, metaclass=SerializerMetaclass):
    def __init__(self, instance=None, data=None, **kwargs):
        if kwargs.pop('included', False):
            self._included = []
        else:
            self._included = None
        super().__init__(instance, data, **kwargs)
    
    @property
    async def included(self):
        if self._included is not None and not self._included:
            await self.to_representation(self.instance)
        return self._included
    
    async def _add_value(self, dictionary, keys, value):
        for key in keys:
            if not dictionary.get(key):
                dictionary[key] = {'data': []}
            dictionary[key]['data'].append(value)
    
    async def _serialize_included(self, objects_list):
        for obj in objects_list:
            fields = await get_field_info(obj)
            data_included = {'type': obj.__class__.__name__.lower(), 'id': obj.id}
            for attribute in fields.fields.keys():
                if not data_included.get('attributes'):
                    data_included['attributes'] = {}
                data_included['attributes'][attribute] = getattr(obj, attribute)
            for relationship in fields.forward_relations.keys():
                if not data_included.get('relationships'):
                    data_included['relationships'] = {}
                objects_list = getattr(obj, relationship)
                try:
                    objects_list = objects_list.all()
                except AttributeError:
                    objects_list = [objects_list]
                if not objects_list:
                    continue
                objects_list = [await JSONAPITypeIdSerializer(inner_obj).data 
                                for inner_obj in objects_list]
                objects_list = [val for val in objects_list]
                data_included['relationships'][relationship] = {
                    'data': objects_list if len(objects_list) > 1 else objects_list.pop()
                }
            self._included.append(data_included)
    
    async def to_internal_value(self, data):
        ret = {}
        errors = {}
        fields = self.fields
        for name, field in fields.items():
            value = await self.get_value(name, data)
            if not value and field.required:
                errors[f'relationships.{name}.data'] = ValidationError(
                    'This field may not be null.'
                ).detail
                continue
            field = field.child if hasattr(field, 'child') else field
            value = [value] if type(value) != list else value
            for obj in value:
                try:
                    validated_value = field.__class__(data=obj).run_validation(obj)
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
        fields = self.fields
        data = {name: await self.get_value(
            name, {key: getattr(instance, key) for key in fields.keys()}
        ) for name in self.fields.keys()}
        for key, val in data.items():
            if hasattr(val, 'all'):
                objects_list = []
                async for e in val.all():
                    objects_list.append(e)
            else:
                objects_list = [val]
            value = [await JSONAPITypeIdSerializer(obj).data for obj in objects_list]
            data[key] = {'data': value.pop() if len(value) == 1 else value}
            if self._included is not None:
                await self._serialize_included(objects_list)
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
        self.child.field_name = self
        self.child.parent = self
    
    async def to_representation(self, data):
        self.iterable = data.all() if isinstance(data, BaseManager) else data
        data = {'data': []}
        included = {}
        for obj in self.iterable:
            obj_data = await self.child.__class__(obj).data
            included_obj_data = obj_data.pop('included', None)
            if included_obj_data is not None:
                included.update({f'{obj["type"]}_{obj["id"]}': dict(obj) for obj 
                                in await included_obj_data})
            data['data'].append(*obj_data.pop('data'))
        if included:
            data['included'] = sorted(
                list(included.values()), 
                key=lambda x: (x['type'], x['id'])
            )
        return data
    
    @property
    async def data(self):
        if hasattr(self, 'initial_data') and not hasattr(self, '_validated_data'):
            raise AssertionError('you must call `.is_valid()` before attempting '
                                 'to access the serialized `.data` representation.\n')
        if not hasattr(self, '_data'):
            if self.instance is not None and not getattr(self, '_errors', None):
                self._data = await self.to_representation(self.instance)
            elif hasattr(self, '_validated_data') and not getattr(self, '_errors', None):
                self._data = await self.validated_data
            else:
                self._data = self.get_initial()
        return ReturnDict(self._data, serializer=self)


class JSONAPISerializer(JSONAPIBaseSerializer, metaclass=SerializerMetaclass):
    included = JSONField()
    
    class Type(JSONAPITypeIdSerializer):
        pass
    
    class Attributes(JSONAPIAttributesSerializer):
        pass
    
    class Relationships(JSONAPIRelationsSerializer):
        pass
    
    class Meta:
        list_serializer_class = JSONAPIManySerializer
    
    async def get_value(self, field_name, dictionary=None):
        return dictionary.get(field_name, None)
    
    async def to_internal_value(self, data):
        ret = {}
        errors = {}
        fields = self.fields
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
        fields = self.fields
        obj_map = {**await self.Type(instance).data}
        serializer_map = {
            'attributes': fields['attributes'].__class__(instance),
            'relationships': fields['relationships'].__class__(instance, included=True)
        }
        for key, val in serializer_map.items():
            if len(val._declared_fields):
                try:
                    obj_map[key] = await val.data
                except SynchronousOnlyOperation as e:
                    raise NotSelectedForeignKey from e
            else:
                obj_map[key] = {}
        obj_map['included'] = serializer_map['relationships'].included
        return {'data': [{
            **{name: await self.get_value(name, obj_map) for name in 
                fields.keys() if name in obj_map and name != 'included'}
            }], 'included': await self.get_value('included', obj_map)}
