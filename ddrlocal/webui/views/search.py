from datetime import datetime
from decimal import Decimal
import logging
logger = logging.getLogger(__name__)

from dateutil import parser

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.core.urlresolvers import reverse
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import Http404, get_object_or_404, render_to_response
from django.template import RequestContext
from django.utils.http import urlquote  as django_urlquote

from elasticsearch import Elasticsearch

from DDR import docstore, models
from webui import tasks
from webui.forms.search import SearchForm, IndexConfirmForm, DropConfirmForm

BAD_CHARS = ('{', '}', '[', ']')


# helpers --------------------------------------------------------------

def kosher( query ):
    for char in BAD_CHARS:
        if char in query:
            return False
    return True

def make_object_url( parts ):
    """Takes a list of object ID parts and returns URL for that object.
    """
    if len(parts) == 6: return reverse('webui-file', args=parts)
    elif len(parts) == 4: return reverse('webui-entity', args=parts)
    elif len(parts) == 3: return reverse('webui-collection', args=parts)
    return None


def massage_query_results( results, thispage, size ):
    objects = docstore.massage_query_results(results, thispage, size)
    results = None
    for o in objects:
        if not o.get('placeholder',False):
            # add URL
            parts = models.split_object_id(o['id'])
            parts = parts[1:]
            o['absolute_url'] = make_object_url(parts)
    return objects


# views ----------------------------------------------------------------

def index( request ):
    return render_to_response(
        'webui/search/index.html',
        {'hide_header_search': True,
         'search_form': SearchForm,},
        context_instance=RequestContext(request, processors=[])
    )

def results( request ):
    """Results of a search query or a DDR ID query.
    """
    template = 'webui/search/results.html'
    context = {
        'hide_header_search': True,
        'query': '',
        'error_message': '',
        'search_form': SearchForm(),
        'paginator': None,
        'page': None,
        'filters': None,
        'sort': None,
    }
    context['query'] = request.GET.get('query', '')
    # silently strip out bad chars
    query = context['query']
    for char in BAD_CHARS:
        query = query.replace(char, '')
        
    if query:
        context['search_form'] = SearchForm({'query': query})
        
        # prep query for elasticsearch
        model = request.GET.get('model', None)
        filters = {}
        fields = docstore.all_list_fields()
        sort = {'record_created': request.GET.get('record_created', ''),
                'record_lastmod': request.GET.get('record_lastmod', ''),}
        
        # do query and cache the results
        results = docstore.search(settings.DOCSTORE_HOSTS, settings.DOCSTORE_INDEX,
                                  query=query, filters=filters,
                                  fields=fields, sort=sort)
        
        if results.get('hits',None) and not results.get('status',None):
            # OK -- prep results for display
            thispage = request.GET.get('page', 1)
            #assert False
            objects = massage_query_results(results, thispage, settings.RESULTS_PER_PAGE)
            paginator = Paginator(objects, settings.RESULTS_PER_PAGE)
            page = paginator.page(thispage)
            context['paginator'] = paginator
            context['page'] = page
        else:
            # FAIL -- elasticsearch error
            context['error_message'] = 'Search query "%s" caused an error. Please try again.' % query
            return render_to_response(
                template, context, context_instance=RequestContext(request, processors=[])
            )
    
    return render_to_response(
        template, context, context_instance=RequestContext(request, processors=[])
    )

def admin( request ):
    """Administrative stuff like re-indexing.
    """
    server_info = []
    es = Elasticsearch(hosts=settings.DOCSTORE_HOSTS)
    ping = es.ping()
    no_indices = True
    if ping:
        info = es.info()
        status = es.indices.status()
        
        info_status = info['status']
        if info_status == 200:
            info_status_class = 'success'
        else:
            info_status_class = 'error'
        server_info.append( {'label':'status', 'data':info_status, 'class':info_status_class} )
        
        shards_success = status['_shards']['successful']
        shards_failed = status['_shards']['failed']
        if shards_failed == 0:
            shards_success_class = 'success'
            shards_failed_class = 'success'
        else:
            shards_success_class = 'error'
            shards_failed_class = 'error'
        server_info.append( {'label':'shards(successful)', 'data':shards_success, 'class':shards_success_class} )
        server_info.append( {'label':'shards(failed)', 'data':shards_failed, 'class':shards_failed_class} )
        
        for name in status['indices'].keys():
            no_indices = False
            server_info.append( {'label':name, 'data':'', 'class':''} )
            size = status['indices'][name]['index']['size_in_bytes']
            ONEPLACE = Decimal(10) ** -1
            size_nice = Decimal(size/1024/1024.0).quantize(ONEPLACE)
            size_formatted = '%sMB (%s bytes)' % (size_nice, size)
            num_docs = status['indices'][name]['docs']['num_docs']
            server_info.append( {'label':'size', 'data':size_formatted, 'class':'info'} )
            server_info.append( {'label':'documents', 'data':num_docs, 'class':'info'} )
            
    indexform = IndexConfirmForm()
    dropform = DropConfirmForm()
    return render_to_response(
        'webui/search/admin.html',
        {'ping': ping,
         'no_indices': no_indices,
         'server_info': server_info,
         'indexform': indexform,
         'dropform': dropform,},
        context_instance=RequestContext(request, processors=[])
    )

def reindex( request ):
    if request.method == 'POST':
        form = IndexConfirmForm(request.POST)
        if form.is_valid():
            tasks.reindex_and_notify(request)
    return HttpResponseRedirect( reverse('webui-search-admin') )

def drop_index( request ):
    if request.method == 'POST':
        form = DropConfirmForm(request.POST)
        if form.is_valid():
            docstore.delete_index(settings.DOCSTORE_HOSTS, settings.DOCSTORE_INDEX)
            messages.error(request, 'Search indexes dropped. Click "Re-index" to reindex your collections.')
    return HttpResponseRedirect( reverse('webui-search-admin') )
