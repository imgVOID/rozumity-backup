from rest_framework import serializers
from asgiref.sync import sync_to_async


class BaseJSONAPI(serializers.BaseSerializer):
    def _get_attrs_rels(self, instance):
        attributes = {}
        relations = {}
        for field in instance._meta.get_fields():
            if any((
                field.auto_created, 
                field.__class__.__name__ == 'ManyRelatedManager'
            )):
                continue
            elif field.related_model:
                instance_map = relations
                data = {'relation_name': str(field).split('.')[-1],
                        'type': field.related_model.__name__.lower()}
            else:
                instance_map = attributes
                data = str(field).split('.')[-1]
            instance_map[field.value_from_object(instance)] = data
        else:
            return attributes, relations
    
    def _map_attributes(self, attributes):
        return {v: k for (k,v) in attributes.items()}
    
    def _map_relationships(self, relationships):
        return {
            v['relation_name']: {'data': {'id': k, 'type': v['type']}}
            for (k, v) in relationships.items()
        }
    
    def _get_mapped_instance(self, instance_type, instance_id, 
                      attributes, relationships):
        data = {'type': instance_type, 'id': instance_id}
        if attributes:
            attributes = self._map_attributes(attributes)
            data['attributes'] = attributes
        if relationships:
            relationships = self._map_relationships(relationships)
            data['relationships'] = relationships
        return data
        
    
    def to_representation(self, instance):
        return self._get_mapped_instance(
            instance.__class__.__name__.lower(), 
            instance.id, *self._get_attrs_rels(instance)
        )


class JSONAPISerializer(BaseJSONAPI):
    def _get_attrs_rels(self, instance):
        return {
            str(field).split('.')[-1]: field.value_from_object(instance)
            for field in instance._meta.get_fields()
            if not field.auto_created and not field.related_model
        }, {}
    
    def _map_attributes(self, attributes):
        return attributes


class JSONAPIRelSerializer(BaseJSONAPI):
    def _get_mapped_instance(self, instance_type, instance_id, 
                             attributes, relationships):
        return {
            'type': instance_type, 
            'id': instance_id,
            'attributes': self._map_attributes(attributes),
            'relationships': self._map_relationships(relationships)
        }


class JSONAPIRelToManySerializer(BaseJSONAPI):
    pass


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
