from contextlib import suppress
from django.utils.translation import gettext_lazy as _
from rest_framework.settings import api_settings
from rest_framework.response import Response
from rest_framework.utils.urls import remove_query_param, replace_query_param
from asgiref.sync import sync_to_async


class LimitOffsetAsyncPagination:
    default_limit = api_settings.PAGE_SIZE
    limit_query_param = 'page[limit]'
    limit_query_description = _('Number of results to return per page.')
    offset_query_param = 'page[offset]'
    offset_query_description = _('The initial index from which to return the results.')
    max_limit = None
    template = 'rest_framework/pagination/numbers.html'

    @staticmethod
    async def _replace_query_param(*args, **kwargs):
        return await sync_to_async(replace_query_param)(*args, **kwargs)
    
    @staticmethod
    async def _remove_query_param(*args, **kwargs):
        return await sync_to_async(remove_query_param)(*args, **kwargs)
    
    @staticmethod
    async def _encode_url_parameters(url):
        """
        Encode unicode symbols in url parameters
        """
        return url.replace('%5B', '[').replace('%5D', ']')
    
    @staticmethod
    async def _positive_int(integer_string, strict=False, cutoff=None):
        """
        Cast a string to a strictly positive integer.
        """
        ret = int(integer_string)
        if ret < 0 or (ret == 0 and strict):
            raise ValueError()
        if cutoff:
            return min(ret, cutoff)
        return ret
    
    async def _get_absolute_uri(self):
        return await sync_to_async(self.request.build_absolute_uri)()

    async def paginate_queryset(self, queryset, request, view=None):
        self.request = request
        self.limit = await self.get_limit(request)
        if self.limit is None:
            return None

        self.count = await self.get_count(queryset)
        self.offset = await self.get_offset(request)
        if self.count > self.limit and self.template is not None:
            self.display_page_controls = True

        if self.count == 0 or self.offset > self.count:
            return []
        return list(queryset[self.offset:self.offset + self.limit])

    async def get_paginated_response(self, data):
        links = {
            'self': await self._encode_url_parameters(
                await self._get_absolute_uri()
            )
        }
        next = await self.get_next_link()
        prev = await self.get_previous_link()
        if next:
            links['next'] = next
        if prev:
            links['prev'] = prev
        links['last'] = await self.get_last_link()
        links = {'links': links}
        try:
            links = {**links, **data}
        except TypeError:
            raise TypeError('Serializer data must be a valid dictionary.')
        else:
            return Response(links, status=200)

    async def get_paginated_response_schema(self, schema=None):
        schema = schema if schema else {
            'data': {
                'type': 'list',
                'nullable': False,
                'format': 'list_objects_jsonapi',
                'example': [{
                    'type': 'account', 'id': 1, 'attributes': {}, 
                    'relationships': {'profile': {'type': 'profile', 'id': 1}}
                }]
            },
            'included': {
                'type': 'list',
                'nullable': False,
                'format': 'list_objects_jsonapi',
                'example': [{'type': 'profile', 'id': 1, 'attributes': {}}]
            }
        }
        return {
            'links': {
                'self': {
                    'type': 'string',
                    'nullable': False,
                    'format': 'uri',
                    'example': f'http://api.example.org/accounts/?{self.offset_query_param}=200&{self.limit_query_param}=100',
                },
                'prev': {
                    'type': 'string',
                    'nullable': True,
                    'format': 'uri',
                    'example': f'http://api.example.org/accounts/?{self.offset_query_param}=100&{self.limit_query_param}=100',
                },
                'next': {
                    'type': 'string',
                    'nullable': True,
                    'format': 'uri',
                    'example': f'http://api.example.org/accounts/?{self.offset_query_param}=300&{self.limit_query_param}=100',
                },
                'last': {
                    'type': 'string',
                    'nullable': False,
                    'format': 'uri',
                    'example': f'http://api.example.org/accounts/?{self.offset_query_param}=400&{self.limit_query_param}=100',
                }
            }, **schema
        }
    
    async def get_next_link(self):
        if self.offset + self.limit >= self.count:
            return None
        else:
            url = await self._replace_query_param(
                await self._get_absolute_uri(), 
                self.offset_query_param, 
                self.offset + self.limit
            )
        if self.limit == self.default_limit:
            url = await self._remove_query_param(url, self.limit_query_param)
        else:
            url = await self._replace_query_param(url, self.limit_query_param, self.limit)
        return await self._encode_url_parameters(url)

    async def get_previous_link(self):
        if self.offset <= 0:
            return None
        elif self.offset - self.limit <= 0:
            url = await self._remove_query_param(
                await self._get_absolute_uri(), 
                self.offset_query_param
            )
        else:
            url = await self._replace_query_param(
                await self._get_absolute_uri(), 
                self.offset_query_param, 
                self.offset - self.limit
            )
        if self.limit == self.default_limit:
            url = await self._remove_query_param(url, self.limit_query_param)
        else:
            url = await self._replace_query_param(url, self.limit_query_param, self.limit)
        return await self._encode_url_parameters(url)
    
    async def get_last_link(self):
        url = await self._replace_query_param(
            await self._get_absolute_uri(), 
            self.offset_query_param, 
            self.count // self.limit * self.limit
        )
        if self.limit == self.default_limit:
            url = await self._remove_query_param(url, self.limit_query_param)
        else:
            url = await self._replace_query_param(url, self.limit_query_param, self.limit)
        return await self._encode_url_parameters(url)

    async def get_limit(self, request):
        supress_async = sync_to_async(suppress)
        if self.limit_query_param:
            with await supress_async(KeyError, ValueError):
                return await self._positive_int(
                    request.query_params[self.limit_query_param],
                    strict=True,
                    cutoff=self.max_limit
                )
        return self.default_limit

    async def get_offset(self, request):
        try:
            return await self._positive_int(
                request.query_params[self.offset_query_param],
            )
        except (KeyError, ValueError):
            return 0
    
    async def get_count(self, queryset):
        try:
            return await queryset.count()
        except (AttributeError, TypeError):
            return len(queryset)


if __name__ != '__main__':
    LimitOffsetAsyncPagination = sync_to_async(LimitOffsetAsyncPagination).func()
