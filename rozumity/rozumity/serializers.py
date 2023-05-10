from django.db.models.manager import BaseManager
from django.core.exceptions import ImproperlyConfigured, SynchronousOnlyOperation
from rest_framework.utils.serializer_helpers import ReturnDict
from django.forms.models import model_to_dict
from rest_framework import serializers
from rest_framework.utils.model_meta import get_field_info
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


class JSONAPIDictSerializer(serializers.ListSerializer):

    def to_representation(self, data):
        """
        List of object instances -> List of dicts of primitive datatypes.
        """
        # Dealing with nested relationships, data can be a Manager,
        # so, first get a queryset from the Manager if needed
        iterable = data.all() if isinstance(data, BaseManager) else data
        mapped_instances = []
        mapped_instances_included = {}
        for obj in iterable:
            try:
                attributes, relations, included = self.child._split_attrs(obj)
            except AttributeError as e:
                raise NotPrefetchedManyToMany from e
            except SynchronousOnlyOperation as e:
                raise NotSelectedForeignKey from e
            mapped_instances.append(self.child._get_mapped_instance(
                obj.__class__.__name__.lower(), 
                obj.id, attributes, relations
            ))
            for name, relation in included.items():
                if not mapped_instances_included.get(name):
                    mapped_instances_included[name] = relation
        return mapped_instances, mapped_instances_included.values()

    @property
    def data(self):
        data = super().data
        return ReturnDict({'data': data[0], 'included':data[1]}, serializer=self)


# Create possibility to obtain a ManyToMany clild's relation objects
class JSONAPISerializer(serializers.BaseSerializer):
    class Meta:
        list_serializer_class = JSONAPIDictSerializer
    
    @staticmethod
    def _get_objects_attrs(objects_list, field_info):
        objects_list = [{
            name: getattr(relation_obj, name) for name, _ 
            in field_info.items()
        } for relation_obj in objects_list]
        
        return objects_list
    
    def _get_mapped_instance(self, instance_type, instance_id, 
                             attributes, relationships):
        data = {'type': instance_type, 'id': instance_id}
        if attributes:
            data['attributes'] = attributes
        if relationships:
            data['relationships'] = relationships
        return data
    
    # Create possibility to obtain a ManyToMany clild's relation objects
    def _get_included(self, name, relation_type, relation_objects, relation_field_info):
        included = {}
        relation_attributes = self._get_objects_attrs(
            relation_objects, relation_field_info.fields
        )
        relation_relations = []
        for i in range(len(relation_objects)):
            relation_relations.append({})
            for name, field in relation_field_info.forward_relations.items():
                relation_relations[i].update({name: self._get_mapped_instance(
                    field.related_model.__name__.lower(), 
                    getattr(relation_objects[i], name,).id, None, None
                )})
            key = f'{relation_type}_{relation_objects[i].id}'
            try:
                included[key]
            except KeyError:
                obj = relation_objects[i]
                included[key] = self._get_mapped_instance(
                    obj.__class__.__name__.lower(), obj.id, 
                    relation_attributes[i], relation_relations[i]
                )
        return included
    
    def _split_attrs(self, instance):
        field_info = get_field_info(type(instance))
        attributes = self._get_objects_attrs([instance], field_info.fields).pop()
        relations = {}
        included = {}
        for name, field in field_info.forward_relations.items():
            relation_type = field.related_model.__name__.lower()
            if field.to_many:
                relation_objects = getattr(instance, f'{name}_set')
            else:
                relation_objects = [getattr(instance, name)]
            relation_field_info = get_field_info(type(relation_objects[0]))
            try:
                relations[name]
            except KeyError:
                data = [{'type': relation_type, 'id': obj.id} 
                        for obj in relation_objects]
                relations[name] = {'data': data if len(data) > 1 else data[0]} 
                included.update(self._get_included(
                    name, relation_type, relation_objects, relation_field_info
                ))
        return attributes, relations, included
        
    def to_representation(self, instance):
        data = {'data': [], 'included': []}
        mapped_instances_included = {}
        try:
            attributes, relations, included = self._split_attrs(instance)
        except AttributeError as e:
            raise NotPrefetchedManyToMany from e
        except SynchronousOnlyOperation as e:
            raise NotSelectedForeignKey from e
        data['data'] = [self._get_mapped_instance(
            instance.__class__.__name__.lower(), 
            instance.id, attributes, relations
        )]
        for name, relation in included.items():
            if not mapped_instances_included.get(name):
                mapped_instances_included[name] = relation
        data['included'].extend(mapped_instances_included.values())
        return data

    @property
    def data(self):
        data = super().data
        return ReturnDict({'data': data[0], 'included':data[1]}, serializer=self)


class JSONAPIManager:
    def __init__(self, objects, serializer, related=None, 
                 related_serializer=None, request=None):
        try:
            iter(objects)
        except TypeError:
            objects = [objects]
        finally:
            self._objects = objects
        try:
            iter(related)
        except TypeError:
            related = [related]
        finally:
            self._related = related
        self._request = request
        self._related_url = ''
        self.serializer = serializer
        self.related_serializer = related_serializer

    async def _get_absolute_uri(self):
        return await sync_to_async(self._request.build_absolute_uri)()
    
    async def get_link(self):
        return await self._get_absolute_uri()
    
    async def get_link_related(self):
        url = await self.get_link()
        return url.split('api/')[0] + 'api/' + self._related_url

    @property
    async def data(self):
        data = {}
        if self._objects and self.serializer:
            serialize = self.serializer(
                self._objects, many=True
            )
            data['data'] = serialize.data
            link = await self.get_link()
            for obj_map in data['data']:
                obj_map['links'] = {
                    'self': f'{link}{obj_map["id"]}'
                }
        if self._related and self.related_serializer:
            serialize = self.related_serializer(
                self._related, many=True
            )
            data['included'] = serialize.data
            link = await self.get_link_related()
            for obj_map in data['included']:
                obj_map['links'] = {
                    'self': f'{link}{obj_map["id"]}'
                }
                print(await self.get_link_related())
        return data

    @property
    def data_sync(self):
        data = {}
        if self._objects and self.serializer:
            serialize = self.serializer(
                self._objects, many=True
            )
            data['data'] = serialize.data
        if self._related and self.related_serializer:
            serialize = self.related_serializer(
                self._related, many=True
            )
            data['included'] = serialize.data
        return data
