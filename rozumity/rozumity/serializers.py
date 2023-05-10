from django.db.models.manager import BaseManager
from django.core.exceptions import ImproperlyConfigured, SynchronousOnlyOperation
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
            try:
                attributes, relations, included = self.child._get_attrs_rels_incl(obj)
            except AttributeError as e:
                raise ImproperlyConfigured(
                    'Model.objects.prefetch_related(Prefetch("<relation_field_name>", '
                    'to_attr="<fied_name>_set")) '
                    'must be specified'
                ) from e
            except SynchronousOnlyOperation as e:
                raise ImproperlyConfigured(
                    'Model.objects.prefetch_related("<relation_field_name>__<inner_relation_field_name") '
                    'must be specified'
                ) from e
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
            relation_type = field.related_model.__name__.lower()
            if field.to_many:
                relation_objects = getattr(instance, name + '_set')
                relation_field_info = get_field_info(type(relation_objects[0]))
                try:
                    relations[name]
                except KeyError:
                    relations[name] = {'data': [
                        {'type': relation_type, 'id': obj.id}
                        for obj in relation_objects
                    ]}
                relation_attributes = [{
                    name: getattr(relation_obj, name) for name, _ 
                    in relation_field_info.fields.items()
                } for relation_obj in relation_objects]
                relation_relations = []
                for i in range(len(relation_objects)):
                    # included
                    relation_relations.append({})
                    for name, field in relation_field_info.forward_relations.items():
                        relation_relations[i].update({name:{
                            'type': relation_type,
                            'id': getattr(instance, name).id
                        }})
                    key = f'{relation_type}_{relation_objects[i].id}'
                    try:
                        included[key]
                    except KeyError:
                        relation_map = {
                            'type': relation_type, 'id':relation_objects[i].id
                        }
                        if relation_attributes[i]:
                            relation_map['attributes'] = relation_attributes[i]
                        if relation_relations[i]:
                            relation_map['relationships'] = relation_relations[i]
                        included[key] = relation_map
            else:
                relation_objects = [getattr(instance, name)]
                relation_field_info = get_field_info(type(relation_objects[0]))
                try:
                    relations[name]
                except KeyError:
                    relations[name] = {'data': {
                        'type': relation_type,
                        'id': getattr(instance, name).id
                    }}
            # included
            relation_attributes = [{
                name: getattr(relation_obj, name) for name, _ 
                in relation_field_info.fields.items()
            } for relation_obj in relation_objects]
            relation_relations = []
            for i in range(len(relation_objects)):
                relation_relations.append({})
                for name, field in relation_field_info.forward_relations.items():
                    relation_type = field.related_model.__name__.lower()
                    relation_object_relation_object = getattr(relation_objects[i], name)
                    relation_relations[i].update({name:{
                        'type': relation_type,
                        'id': relation_object_relation_object.id
                    }})
                key = f'{relation_type}_{relation_objects[i].id}'
                try:
                    included[key]
                except KeyError:
                    relation_map = {
                        'type': relation_type, 'id':relation_objects[i].id
                    }
                    if relation_attributes[i]:
                        relation_map['attributes'] = relation_attributes[i]
                    if relation_relations[i]:
                        relation_map['relationships'] = relation_relations[i]
                    included[key] = relation_map
        return attributes, relations, included
    
    def _get_mapped_instance(self, instance_type, instance_id, 
                             attributes, relationships):
        data = {'type': instance_type, 'id': instance_id}
        if attributes:
            data['attributes'] = attributes
        if relationships:
            data['relationships'] = relationships
        return data
        
    def to_representation(self, instance):
        data = {'data': [], 'included': []}
        mapped_instances_included = {}
        try:
            attributes, relations, included = self._get_attrs_rels_incl(instance)
        except AttributeError as e:
            raise ImproperlyConfigured(
                'prefetch_related("<field_name>", to_attr="<fied_name>_set")'
                'must be specified'
            ) from e
        data['data'] = [self._get_mapped_instance(
            instance.__class__.__name__.lower(), 
            instance.id, attributes, relations
        )]
        data['included'] = [self._get_mapped_instance(
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
