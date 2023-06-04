from rest_framework.views import exception_handler

def custom_jsonapi_exception_handler(exc, context):
    response = exception_handler(exc, context)
    # Now it's time for JSONAPI formatting. 
    if response is not None:
        response.data = {"jsonapi": { "version": "1.1" }, 'errors': [{
            'code': response.status_code, 
            'source': {'pointer': context.get('request').get_full_path()}, 
            **response.data
        }]}
    return response
