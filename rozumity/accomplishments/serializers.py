
from rozumity.serializers import JSONAPISerializer, JSONAPIRelationsSerializer, JSONAPITypeIdSerializer, JSONAPIAttributesSerializer, ValidateFieldType
from cities_light.models import Country
from rest_framework import serializers
from .models import University


class UniversityAttributesSerializer(JSONAPIAttributesSerializer):
    title = serializers.CharField(validators=[ValidateFieldType(str)])


class UniversityRelationsSerializer(JSONAPIRelationsSerializer):
    country = JSONAPITypeIdSerializer()


class UniversitySerializer(JSONAPISerializer):
    attributes = UniversityAttributesSerializer()
    relationships = UniversityRelationsSerializer()


class TestAttributesSerializer(JSONAPIAttributesSerializer):
    title = serializers.CharField(validators=[ValidateFieldType(str)])


class TestRelationsSerializer(JSONAPIRelationsSerializer):
    city = JSONAPITypeIdSerializer()
    country = serializers.ListField(child=JSONAPITypeIdSerializer())


class TestSerializer(JSONAPISerializer):
    attributes = TestAttributesSerializer()
    relationships = TestRelationsSerializer()
