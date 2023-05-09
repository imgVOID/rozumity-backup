from django.db.models.manager import BaseManager
from rest_framework.utils.serializer_helpers import ReturnDict
from django.forms.models import model_to_dict
from rest_framework import serializers
from rest_framework.utils.model_meta import get_field_info
from asgiref.sync import sync_to_async



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
            attributes, relations, included = self.child._get_attrs_rels_incl(obj)
            mapped_instances.append(self.child._get_mapped_instance(
                obj.__class__.__name__.lower(), 
                obj.id, attributes, relations, included
            ))
            for name, relation in included.items():
                if not mapped_instances_included.get(name):
                    mapped_instances_included[name] = {}
                for rel in relation:
                    field_rel = mapped_instances_included[name]
                    if field_rel.get(rel['id']) != rel['id']:
                        mapped_instances_included[name] = {rel['id']: included}
        # TODO: make dict key with field_name+id and check it without nested loop
        mapped_instances_included_result = [
            v for v2 in [
                v for v2 in [
                    v2[v].values() for v2 in 
                    mapped_instances_included.values() 
                    for v in v2] for v in v2
            ] for v in v2
        ]
        return mapped_instances, mapped_instances_included_result

    @property
    def data(self):
        data = super().data
        return ReturnDict({'data': data[0], 'included':data[1]}, serializer=self)


class JSONAPISerializer(serializers.BaseSerializer):
    class Meta:
        list_serializer_class = JSONAPIDictSerializer
    
    def _get_attrs_rels_incl(self, instance):
        included = {}
        attributes = {}
        relations = {}
        field_info = get_field_info(type(instance))
        attributes = {
            name: getattr(instance, name) for name, _ 
            in field_info.fields.items()
        }
        relations = {}
        for name, field in field_info.forward_relations.items():
            relation_obj = getattr(instance, name)
            relation_type = field.related_model.__name__.lower()
            relation_field_info = get_field_info(type(relation_obj))
            relation_attributes = {
                name: getattr(relation_obj, name) for name, _ 
                in relation_field_info.fields.items()
            }
            relation_relations = {}
            for name, field in relation_field_info.forward_relations.items():
                relation_relations[name] = {
                    'type': relation_type,
                    'id': getattr(instance, name).id
                }
            try:
                included[relation_type]
            except KeyError:
                included[relation_type] = []
            relation_map = {
                'type': relation_type, 'id':relation_obj.id, 
                'attributes': relation_attributes, 
            }
            if relation_relations:
                relation_map['attributes'] = relation_attributes
            if relation_relations:
                relation_map['relationships'] = relation_relations
            included[relation_type].append(relation_map)
            relations[name] = {
                'type': relation_type,
                'id': getattr(instance, name).id
            }
        return attributes, relations, included
    
    def _get_mapped_instance(self, instance_type, instance_id, 
                             attributes, relationships, included):
        data = {'type': instance_type, 'id': instance_id}
        if attributes:
            data['attributes'] = attributes
        if relationships:
            data['relationships'] = relationships
        return data
        
    def to_representation(self, instance):
        data = {'data': []}
        attributes, relations, included = self._get_attrs_rels_incl(instance)
        data['data'] = (self._get_mapped_instance(
            instance.__class__.__name__.lower(), 
            instance.id, attributes, relations, included
        ))
        return data


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
