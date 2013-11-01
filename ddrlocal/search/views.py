from datetime import datetime
import logging
logger = logging.getLogger(__name__)

from django.conf import settings
from django.core.urlresolvers import reverse
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import Http404, get_object_or_404, render_to_response
from django.template import RequestContext

import search
from search import tasks
from search.forms import IndexConfirmForm


# helpers --------------------------------------------------------------


# views ----------------------------------------------------------------

def index( request ):
    """Search forms, simple and advanced; links to admin
    """
    confirm = request.GET.get('confirm', None)
    if confirm:
        pass
        # start index task
        # message
        # redirect
    return render_to_response(
        'search/index.html',
        {},
        context_instance=RequestContext(request, processors=[])
    )

def query( request ):
    """Results of a search query.
    """
    model = request.GET.get('model', None)
    q = request.GET.get('query', None)
    hits = search.query(query=q)
    
    def rename(hit, fieldname):
        under = '_%s' % fieldname
        hit[fieldname] = hit.pop(under)
    
    for hit in hits:
        rename(hit, 'index')
        rename(hit, 'type')
        rename(hit, 'id')
        rename(hit, 'score')
        rename(hit, 'source')
        for field in hit['source']['d'][1:]:
            if field.keys():
                if field.keys()[0] == 'id': hit['id'] = field['id']
                if field.keys()[0] == 'title': hit['title'] = field['title']
        if hit.get('id', None):
            if hit['type'] == 'collection':
                repo,org,cid = hit['id'].split('-')
                hit['url'] = reverse('webui-collection', args=[repo,org,cid])
            elif hit['type'] == 'entity':
                repo,org,cid,eid = hit['id'].split('-')
                hit['url'] = reverse('webui-entity', args=[repo,org,cid,eid])
            elif hit['type'] == 'file':
                repo,org,cid,eid,role,sha1 = hit['id'].split('-')
                hit['url'] = reverse('webui-file', args=[repo,org,cid,eid,role,sha1])
    return render_to_response(
        'search/query.html',
        {'hits': hits,
         'query': q,},
        context_instance=RequestContext(request, processors=[])
    )

def admin( request ):
    """Administrative stuff like re-indexing.
    """
    status = search.status()
    if status:
        status['shards'] = status.pop('_shards')
    
    confirm = request.GET.get('confirm', None)
    if confirm:
        pass
        # start index task
        # message
        # redirect
    form = IndexConfirmForm()
    return render_to_response(
        'search/admin.html',
        {'status': status,
         'form': form,},
        context_instance=RequestContext(request, processors=[])
    )

def reindex( request ):
    if request.method == 'POST':
        form = IndexConfirmForm(request.POST)
        if form.is_valid():
            result = tasks.reindex.apply_async( (), countdown=2)
            celery_tasks = request.session.get(settings.CELERY_TASKS_SESSION_KEY, {})
            # IMPORTANT: 'action' *must* match a message in webui.tasks.TASK_STATUS_MESSAGES.
            task = {'task_id': result.task_id,
                    'action': 'search-reindex',
                    'start': datetime.now(),}
            celery_tasks[result.task_id] = task
            request.session[settings.CELERY_TASKS_SESSION_KEY] = celery_tasks
    return HttpResponseRedirect( reverse('search-admin') )
