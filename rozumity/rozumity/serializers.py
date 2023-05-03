from rest_framework import serializers
from django.forms.models import model_to_dict


class BaseJSONAPI(serializers.BaseSerializer):
    def get_jsonapi_body(self, instance):
        fields = model_to_dict(instance)
        del fields['id']
        return {
            'type': instance.__class__.__name__.lower(),
            'id': instance.id,
            'attributes': fields,
            'relations': {}
        }
    
    def to_representation(self, instance):
        return {k: v for (k, v) in 
                self.get_jsonapi_body(instance).items() if v}


class JSONAPI(BaseJSONAPI):
    pass
