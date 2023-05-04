from rest_framework import serializers
from django.forms.models import model_to_dict
from django.db import models


class BaseJSONAPI(serializers.ModelSerializer):
    def get_jsonapi_body(self, type, instance_id, fields, relations={}):
        relations = {k: {'data': {'id': v, 'type': k}} for (k, v) in relations.items()}
        data = {
            'type': type,
            'id': instance_id,
            'attributes': fields,
            'relationships': relations
        }
        if not data['relationships']:
            del data['relationships']
        return data
    
    def to_representation(self, instance):
        fields = [
            f for f in instance._meta.get_fields() 
            if not f.auto_created and f.concrete
        ]
        relations = {
            str(f).split('.')[-1]: f.value_from_object(instance) 
            for f in fields if f.related_model
        }
        fields = {
            str(f).split('.')[-1]: f.value_from_object(instance) 
            for f in fields if not f.related_model
        }
        return self.get_jsonapi_body(
            instance.__class__.__name__.lower(), instance.id, fields, relations
        )


class JSONAPI(BaseJSONAPI):
    pass
