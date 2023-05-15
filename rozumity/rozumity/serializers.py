from copy import deepcopy
from django.db.models.manager import BaseManager
from django.core.exceptions import ImproperlyConfigured, SynchronousOnlyOperation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.apps import apps
from rest_framework import serializers
from django.utils.functional import cached_property
from rest_framework.utils.model_meta import get_field_info
from rest_framework.exceptions import ValidationError
from rest_framework.relations import Hyperlink, PKOnlyObject
from rest_framework.utils.serializer_helpers import (
    BindingDict, BoundField, JSONBoundField, NestedBoundField, ReturnDict
)
from rest_framework.fields import JSONField, CharField, IntegerField, Field, SkipField, set_value, get_error_detail
from accomplishments.models import Test
from asgiref.sync import sync_to_async

BoundField.field_name = ''


class NotSelectedForeignKey(ImproperlyConfigured):
    def __init__(self, message=None):
        self.message = (
            'Model.objects.select_related(<foreign_key_field_name>, ' 
            '<foreign_key_field_name>__<inner_foreign_key_field_name>) '
            'must be specified.'
        )
        super().__init__(self.message)


class NotPrefetchedManyToMany(ImproperlyConfigured):
    def __init__(self, message=None):
        self.message = (
            'Model.objects.prefetch_related(Prefetch("<many_to_many_field_name>", '
            'to_attr="<many_to_many_field_name>_set")) must be specified.'
        )
        super().__init__(self.message)


class ValidateFieldType:
    def __init__(self, python_type):
        self.type = python_type

    def __call__(self, value):
        if type(value) != self.type:
            message = f"The value {value} is wrong type."
            raise serializers.ValidationError(message)


class BaseSerializer(Field):
    def __init__(self, instance=None, data=None, **kwargs):
        self.instance = instance
        if data is not None:
            self.initial_data = data
        self.partial = kwargs.pop('partial', False)
        self._context = kwargs.pop('context', {})
        kwargs.pop('many', None)
        super().__init__(**kwargs)

    def __new__(cls, *args, **kwargs):
        # We override this method in order to automatically create
        # `ListSerializer` classes instead when `many=True` is set.
        if kwargs.pop('many', False):
            return cls.many_init(*args, **kwargs)
        return super().__new__(cls, *args, **kwargs)

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

    @property
    def data(self):
        if hasattr(self, 'initial_data') and not hasattr(self, '_validated_data'):
            msg = (
                'When a serializer is passed a `data` keyword argument you '
                'must call `.is_valid()` before attempting to access the '
                'serialized `.data` representation.\n'
                'You should either call `.is_valid()` first, '
                'or access `.initial_data` instead.'
            )
            raise AssertionError(msg)

        if not hasattr(self, '_data'):
            if self.instance is not None and not getattr(self, '_errors', None):
                self._data = self.to_representation(self.instance)
            elif hasattr(self, '_validated_data') and not getattr(self, '_errors', None):
                self._data = self.to_representation(self.validated_data)
            else:
                self._data = self.get_initial()
        return self._data

    @property
    def errors(self):
        if not hasattr(self, '_errors'):
            msg = 'You must call `.is_valid()` before accessing `.errors`.'
            raise AssertionError(msg)
        return self._errors

    @property
    def validated_data(self):
        if not hasattr(self, '_validated_data'):
            msg = 'You must call `.is_valid()` before accessing `.validated_data`.'
            raise AssertionError(msg)
        return self._validated_data


class SerializerMetaclass(type):
    """
    This metaclass sets a dictionary named `_declared_fields` on the class.
    Any instances of `Field` included as attributes on either the class
    or on any of its superclasses will be include in the
    `_declared_fields` dictionary.
    """

    @classmethod
    def _get_declared_fields(cls, bases, attrs):
        fields = [(field_name, attrs.pop(field_name))
                  for field_name, obj in list(attrs.items())
                  if isinstance(obj, Field)]
        fields.sort(key=lambda x: x[1]._creation_counter)

        # Ensures a base class field doesn't override cls attrs, and maintains
        # field precedence when inheriting multiple parents. e.g. if there is a
        # class C(A, B), and A and B both define 'field', use 'field' from A.
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


class JSONAPIBaseSerializer(BaseSerializer, metaclass=SerializerMetaclass):
    @property
    def fields(self):
        fields = {name: BoundField(field, self.get_value(name), [], name)
                  for name, field in self.get_fields().items()}
        return fields
    
    def is_valid(self, *, raise_exception=False):
        if not hasattr(self, '_validated_data'):
            try:
                self._validated_data = self.to_internal_value(self.initial_data)
            except ValidationError as exc:
                self._validated_data = {}
                self._errors = exc.detail
            else:
                self._errors = {}
        if self._errors and raise_exception:
            raise ValidationError(self.errors)
        return not bool(self._errors)

    def to_internal_value(self, data):
        ret = {}
        fields = self.fields

        for name, field in fields.items():
            try:
                validated_value = field.run_validation(field.value)
            except ValidationError as exc:
                field.errors.extend(exc.detail)
            except DjangoValidationError as exc:
                field.errors.extend(get_error_detail(exc))
            except SkipField:
                pass
            else:
                ret[name] = validated_value
        errors = {name: [str(error) for error in field.errors] 
                  for name, field in fields.items() if field.errors}
        if any(errors.values()):
            raise ValidationError({'errors': errors})
        return ret
    

class JSONAPITypeIdSerializer(JSONAPIBaseSerializer, metaclass=SerializerMetaclass):
    type = CharField(validators=[ValidateFieldType(str)])
    id = IntegerField(validators=[ValidateFieldType(int)])
    
    def get_fields(self):
        return deepcopy(self._declared_fields)
    
    def get_value(self, field_name):
        try:
            dictionary = self.initial_data
        except AttributeError:
            dictionary = {'type': self.instance.__class__.__name__.lower(),
                          'id': self.instance.id}
        return dictionary.get(field_name, None)
    
    def to_representation(self, instance):
        return {name:field.value for name, field in self.fields.items()}


class JSONAPIAttributesSerializer(JSONAPIBaseSerializer, metaclass=SerializerMetaclass):
    title = CharField(validators=[ValidateFieldType(str)])
    
    def get_fields(self):
        if getattr(self, 'instance'):
            return dict(self._context.get('field_info', 
                                        get_field_info(self.instance).fields))
        else:
            return deepcopy(self._declared_fields)
    
    def get_value(self, field_name):
        try:
            return self.initial_data.get('attributes').get(field_name)
        except AttributeError:
            return getattr(self.instance, field_name)
    
    def to_representation(self, instance):
        return {name: field.value for name, field in self.fields.items()}



# try to pass included not in the context but in the bound jsonfield
class JSONAPIRelationsSerializer(JSONAPIBaseSerializer, metaclass=SerializerMetaclass):
    @property
    def fields(self):
        if not self._context.get('included_data'):
            self._context['included_data'] = []
        fields = {}
        for name, field in self.get_fields().items():
            value = []
            to_many = field.to_many
            field = field.model_field
            if to_many:
                objects_list = getattr(self.instance, f'{name}_set')
            else:
                objects_list = [getattr(self.instance, name)]
            for object in objects_list:
                type_id = JSONAPITypeIdSerializer(object).data
                value.append(type_id)
                if self._context.get('is_included_needed'):
                    data_included = {**type_id}
                    data_included['attributes'] = JSONAPIAttributesSerializer(object).data
                    relatons = self.__class__(object).data
                    if relatons:
                        data_included['relations'] = relatons
                    self._context['included_data'].append(data_included)
            fields[name] = BoundField(
                field, {'data': value.pop() if len(value) == 1 else value}, [], name
            )
        return fields
        
    def get_fields(self):
        return dict(self._context.get('field_info', 
                                      get_field_info(self.instance).forward_relations))
        
    def to_representation(self, instance):
        try:
            data = {name: field.value for name, field 
                    in self.fields.items()}
        except AttributeError as e:
            raise NotPrefetchedManyToMany from e
        except SynchronousOnlyOperation as e:
            raise NotSelectedForeignKey from e
        else:
            return data


class JSONAPIManySerializer(serializers.ListSerializer):
    def to_representation(self, data):
        self.iterable = data.all() if isinstance(data, BaseManager) else data
        field_info = get_field_info(self.iterable[0])
        data = {'data': []}
        included = {}
        for obj in self.iterable:
            obj_data = self.child.__class__(
                obj, context={**self._context, 'field_info': field_info}
            ).data
            included.update({f'{obj["type"]}_{obj["id"]}': dict(obj) for obj 
                             in obj_data.pop('included')})
            data['data'].append(*obj_data.pop('data'))
        if included:
            data['included'] = sorted(
                list(included.values()), 
                key=lambda x: (x['type'], x['id'])
            )
        return data
    
    @property
    def data(self):
        if hasattr(self, 'initial_data') and not hasattr(self, '_validated_data'):
            raise AssertionError('you must call `.is_valid()` before attempting '
                                 'to access the serialized `.data` representation.\n')
        if not hasattr(self, '_data'):
            if self.instance is not None and not getattr(self, '_errors', None):
                self._data = self.to_representation(self.instance)
            elif hasattr(self, '_validated_data') and not getattr(self, '_errors', None):
                self._data = self.to_representation(self.validated_data)
            else:
                self._data = self.get_initial()
        return ReturnDict(self._data, serializer=self)


class JSONAPISerializer(BaseSerializer):
    type = CharField()
    id = IntegerField()
    attributes = JSONAPIAttributesSerializer()
    relationships = JSONAPIRelationsSerializer()
    included = JSONField()
    
    class Meta:
        list_serializer_class = JSONAPIManySerializer
    
    @cached_property
    def fields(self):
        fields = self.get_fields()
        field_info = self._context.get('field_info', get_field_info(self.instance))
        initial_data = JSONAPITypeIdSerializer(self.instance).data
        relationships_serializer = self.relationships.__class__(self.instance, context={
            'field_info': field_info.forward_relations, 'is_included_needed': True
        })
        for title in ['type', 'id']:
            fields[title] = BoundField(fields[title], initial_data[title], [], title)
        fields['attributes'] = JSONBoundField(
            fields['attributes'], self.attributes.__class__(
                self.instance, context={'field_info': field_info.fields}
            ).data, [], 'attributes'
        )
        fields['relationships'] = JSONBoundField(
            fields['relationships'], relationships_serializer.data, [], 'relationships'
        )
        if fields['relationships'].value:
            included = {f'{obj["type"]}_{obj["id"]}': dict(obj) for obj in 
                        relationships_serializer._context['included_data']}
            fields['included'] = JSONBoundField(
                fields['included'], sorted(
                    list(included.values()), 
                    key=lambda x: (x['type'], x['id'])
                ), [], 'included'
            )
        return fields

    def get_fields(self):
        return {
            'type': self.type, 'id': self.id, 
            'attributes': self.attributes, 
            'relationships': self.relationships,
            'included': self.included
        }

    def to_representation(self, instance):
        try:
            fields = self.fields
        except AttributeError as e:
            raise NotPrefetchedManyToMany from e
        except SynchronousOnlyOperation as e:
            raise NotSelectedForeignKey from e
        else:
            return {'data':[{
                **{name: fields[name].value for name in 
                   ['type', 'id', 'attributes', 'relationships']}
                }], 'included': fields['included'].value}


class JSONAPIRelationsValidationSerializer(BaseSerializer, metaclass=SerializerMetaclass):
    city = JSONAPITypeIdSerializer()
    country = serializers.ListField(child=JSONAPITypeIdSerializer())
    
    def get_value(self, field_name, field=None):
        value = []
        objects_list = self.initial_data.get('relationships').get(field_name).get('data')
        if not hasattr(field, 'child'):
            objects_list = [objects_list]
        for data in objects_list:
            type_id_serializer = JSONAPITypeIdSerializer(data=data)
            type_id_serializer.initial_data = data
            if type_id_serializer.is_valid():
                value.append(type_id_serializer.validated_data)
        return value
    
    @property
    def fields(self):
        fields = {}
        for name, field in self.get_fields().items():
            value = self.get_value(name, field)
            fields[name] = BoundField(
                field, {'data': value.pop() if len(value) == 1 else value}, [], name
            )
        return fields
        
    def get_fields(self):
        return deepcopy(self._declared_fields)
    
    def is_valid(self, *, raise_exception=False):
        if not hasattr(self, '_validated_data'):
            try:
                self._validated_data = self.to_internal_value(self.initial_data)
            except ValidationError as exc:
                self._validated_data = {}
                self._errors = exc.detail
            else:
                self._errors = {}
        if self._errors and raise_exception:
            raise ValidationError(self.errors)
        return not bool(self._errors)
    
    def to_internal_value(self, data):
        ret = {}
        fields = self.fields

        for name, field in fields.items():
            value = field.value['data']
            if hasattr(field._field, 'child'):
                if not ret.get(name):
                    ret[name] = []
                field = field._field.child.__class__
            else:
                value = [value]
                field = field._field.__class__
            for val in value:
                try:
                    serializer = field(data=val)
                    serializer.is_valid()
                    validated_value = serializer.validated_data
                except ValidationError as exc:
                    field.errors.extend(exc.detail)
                except DjangoValidationError as exc:
                    field.errors.extend(get_error_detail(exc))
                except SkipField:
                    pass
                else:
                    if len(value) == 1:
                        ret[name] = validated_value
                    else:
                        if not ret.get(name):
                            ret[name] = []
                        ret[name].append(validated_value)
        errors = {name: [str(error) for error in field.errors] 
                  for name, field in fields.items() if field.errors}
        if any(errors.values()):
            raise ValidationError({'errors': errors})
        return ret


class JSONAPIValidationSerializer(BaseSerializer, metaclass=SerializerMetaclass):
    type = CharField()
    id = IntegerField()
    attributes = JSONAPIAttributesSerializer()
    relationships = JSONAPIRelationsValidationSerializer()
    
    @cached_property
    def fields(self):
        fields = self.get_fields()
        initial_data = self.initial_data
        typeid = JSONAPITypeIdSerializer(data={
            'type': initial_data['type'], 'id': initial_data['id']
        })
        typeid.is_valid()
        typeid = typeid.validated_data
        for title in ['type', 'id']:
            fields[title] = BoundField(fields[title], typeid[title], [], title)
        attributes = fields['attributes'].__class__(data={'attributes':initial_data['attributes']})
        attributes.is_valid()
        fields['attributes'] = JSONBoundField(
            fields['attributes'], attributes.validated_data, [], 'attributes'
        )
        relationships = fields['relationships'].__class__(
            data={'relationships': initial_data['relationships']}
        )
        relationships.is_valid()
        fields['relationships'] = JSONBoundField(
            fields['relationships'], relationships.validated_data, [], 'relationships'
        )
        return fields

    def get_fields(self):
        return deepcopy(self._declared_fields)
    
    def is_valid(self):
        if not hasattr(self, '_validated_data'):
            try:
                self._validated_data = {name: field.value for name, field in self.fields.items()}
            except ValidationError as exc:
                self._validated_data = {}
                self._errors = exc.detail
            else:
                self._errors = {}
        if self._errors:
            raise ValidationError(self.errors)
        return not bool(self._errors)
