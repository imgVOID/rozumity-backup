from django.db.models.manager import BaseManager
from django.core.exceptions import ImproperlyConfigured, SynchronousOnlyOperation
from rest_framework import serializers
from django.utils.functional import cached_property
from rest_framework.utils.model_meta import get_field_info
from rest_framework.relations import Hyperlink, PKOnlyObject
from asgiref.sync import sync_to_async


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


class JSONAPIInitialSerializer(serializers.BaseSerializer):
    def to_representation(self, instance):
        return {
            'type': instance.__class__.__name__.lower(), 
            'id': instance.id
        }


class JSONAPIAttributesSerializer(serializers.BaseSerializer):
    @cached_property
    def fields(self):
        fields = {}
        for name, field in self.get_fields().items():
            setattr(field, 'value', getattr(self.instance, name))
            fields[name] = field
        return fields
    
    def get_fields(self):
        return dict(self._context.get('field_info', 
                                      get_field_info(self.instance).fields))
    
    def to_representation(self, instance):
        self.instance = instance
        return {name: field.value for name, field in self.fields.items()}


class JSONAPIRelationsSerializer(serializers.BaseSerializer):
    @cached_property
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
                data_initial = JSONAPIInitialSerializer(object).data
                value.append(data_initial)
                if self._context.get('is_included_needed'):
                    data_included = {**data_initial}
                    data_included['attributes'] = JSONAPIAttributesSerializer(object).data
                    relatons = self.__class__(object).data
                    if relatons:
                        data_included['relations'] = relatons
                    self._context['included_data'].append(data_included)
            setattr(field, 'value', {
                'data': value.pop() if len(value) == 1 else value
            })
            fields[name] = field
        return fields
    
    def get_fields(self):
        return dict(self._context.get('field_info', 
                                      get_field_info(self.instance).forward_relations))
        
    def to_representation(self, instance):
        self.instance = instance
        try:
            data = {
                name:field.value for name, field 
                in self.fields.items()
            }
        except AttributeError as e:
            raise NotPrefetchedManyToMany from e
        except SynchronousOnlyOperation as e:
            raise NotSelectedForeignKey from e
        else:
            return data


class JSONAPIManySerializer(serializers.ListSerializer):
    def get_fields(self):
        return self._context.get('field_info', get_field_info(self.iterable[0]))
        
    def to_representation(self, data):
        self.iterable = data.all() if isinstance(data, BaseManager) else data
        field_info = self.get_fields()
        data = {'data': []}
        for object in self.iterable:
            object_data = {
                **JSONAPIInitialSerializer(object).data,
                'attributes': JSONAPIAttributesSerializer(
                    object, context={'field_info': field_info.fields}
                ).data
            }
            relationships_serializer = JSONAPIRelationsSerializer(
                object, context={
                    'field_info': field_info.forward_relations, 
                    'is_included_needed': True
                }
            )
            relationships = relationships_serializer.data
            if relationships:
                object_data['relationships'] = relationships
                if not data.get('included'):
                    data['included'] = {}
                for obj in relationships_serializer._context['included_data']:
                    data['included'][f'{obj["type"]}_{obj["id"]}'] = obj
            data['data'].append(object_data)
        if data.get('included'):
            data['included'] = sorted(
                list(data['included'].values()), 
                key=lambda x: (x['type'], x['id'])
            )
        return data
    
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


class JSONAPISerializer(serializers.BaseSerializer):
    class Meta:
        list_serializer_class = JSONAPIManySerializer
    
    def get_fields(self):
        return self._context.get('field_info', get_field_info(self.instance))

    def to_representation(self, instance):
        field_info = self.get_fields()
        data = {'data': []}
        object_data = {
            **JSONAPIInitialSerializer(instance).data,
            'attributes': JSONAPIAttributesSerializer(
                instance, context={'field_info': field_info.fields}
            ).data
        }
        relationships_serializer = JSONAPIRelationsSerializer(
            instance, context={
                'field_info': field_info.forward_relations, 
                'is_included_needed': True
            }
        )
        relationships = relationships_serializer.data
        if relationships:
            object_data['relationships'] = relationships
            if not data.get('included'):
                data['included'] = {}
            for obj in relationships_serializer._context['included_data']:
                data['included'][f'{obj["type"]}_{obj["id"]}'] = obj
        data['data'].append(object_data)
        if data.get('included'):
            data['included'] = sorted(
                list(data['included'].values()), 
                key=lambda x: (x['type'], x['id'])
            )
        return data
    
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
