from datetime import datetime
import json
import logging
logger = logging.getLogger(__name__)
import os
import re

from bs4 import BeautifulSoup
import requests

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.context_processors import csrf
from django.core.files import File
from django.core.paginator import Paginator
from django.core.urlresolvers import reverse
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import Http404, get_object_or_404, render_to_response
from django.template import RequestContext

from DDR import commands
from DDR import docstore

from ddrlocal.models.entity import ENTITY_FIELDS

from storage.decorators import storage_required
from webui import WEBUI_MESSAGES
from webui import api
from webui.decorators import ddrview
from webui.forms import DDRForm
from webui.forms.entities import NewEntityForm, JSONForm, UpdateForm, DeleteEntityForm, RmDuplicatesForm
from webui.mets import NAMESPACES, NAMESPACES_XPATH
from webui.mets import METS_FIELDS, MetsForm
from webui.models import Collection, Entity
from webui.tasks import collection_delete_entity, gitstatus_update
from webui.views.decorators import login_required
from xmlforms.models import XMLModel



# helpers --------------------------------------------------------------

def vocab_terms( fieldname ):
    """Loads and caches list of topics from vocab API.
    
    TODO This should probably be somewhere else
    
    Works with JSON file generated by DDR.vocab.Index.dump_terms_json().
    """
    key = 'vocab:%s' % fieldname
    timeout = 60*60*1  # 1 hour
    data = cache.get(key)
    if not data:
        url = settings.VOCAB_TERMS_URL % fieldname
        r = requests.get(url)
        if r.status_code != 200:
            raise Exception(
                '%s vocabulary file missing: %s' % (fieldname.capitalize(), url))
        data = json.loads(r.text)
        cache.set(key, data, timeout)
    return data

def tagmanager_terms( fieldname ):
    key = 'vocab:%s:tagmanager' % fieldname
    timeout = 60*60*1  # 1 hour
    data = cache.get(key)
    if not data:
        data = []
        vocab = vocab_terms(fieldname)
        for term in vocab['terms']:
            if term.get('path', None):
                text = '%s [%s]' % (term['path'], term['id'])
            else:
                text = '%s [%s]' % (term['title'], term['id'])
            data.append(text)
        #cache.set(key, data, timeout)
    return data
    
def tagmanager_prefilled_terms( entity_terms, all_terms ):
    """Preps list of entity's selected terms for TagManager widget.
    
    TODO This should probably be somewhere else
    
    Topics used in DDR thus far may have different text than new topics,
    though they should have same IDs.
    This function takes 
    
    >>> entity.topics = ['a topic [10]']
    >>> terms = ['A Topic [10]', 'Life The Universe and Everything [42]', ...]
    >>> entity.prefilled_topics(terms)
    ['A Topic [10]']
    
    @param all_terms: list of topics terms
    @param entity_terms: list of terms
    @returns: list of terms for the term IDs
    """
    regex = re.compile('([\d]+)')
    entity_term_ids = []
    for term in entity_terms:
        match = regex.search(term)
        if match:
            for tid in match.groups():
                entity_term_ids.append(tid)
    selected_terms = []
    for term in all_terms:
        match = regex.search(term)
        if match:
            for tid in match.groups():
                if tid in entity_term_ids:
                    selected_terms.append(str(term))
    return selected_terms

def tagmanager_legacy_terms( entity_terms, all_terms ):
    """Returns list of entity terms that do not appear in all_terms.
    
    TODO is "legacy" the right word to use for these?
    
    @param all_terms: list of topics terms
    @param entity_terms: list of terms
    @returns: list of terms
    """
    regex = re.compile('([\d]+)')
    legacy_terms = []
    for term in entity_terms:
        match = regex.search(term)
        if not match:
            legacy_terms.append(str(term))
    return legacy_terms

#def tagmanager_prefilled_terms( entity_terms, all_terms ):
#    """Preps list of selected entity.topics for TagManager widget.
#    
#    TODO This should probably be somewhere else
#    
#    Terms containing IDs will be replaced with canonical term descriptions
#    from the official project controlled vocabulary service.
#    This is because terms used in DDR thus far may have different text
#    than new terms, though they should have same IDs.
#    IMPORTANT: Terms with no ID should be displayed as-is.
#    
#    >>> entity.topics = ['a topic [10]', 'freetext term']
#    >>> terms = ['A Topic [10]', 'freetext term']
#    >>> entity.tagmanager_prefilled_terms(terms)
#    ['A Topic [10]', 'freetext term']
#    
#    @param all_terms: list of terms for FIELD
#    @param entity_terms: list of terms from entity
#    @returns: list of terms for the term IDs
#    """
#    regex = re.compile('([\d]+)')
#    # separate into ID'd and freetext lists.
#    # Add indexs to all_terms as placeholders.
#    terms = []
#    entity_term_ids = {}
#    freetext_terms = {}
#    for n,term in enumerate(entity_terms):
#        terms.append(n)
#        match = regex.search(term)
#        if match:
#            for tid in match.groups():
#                entity_term_ids[n] = tid
#        else:
#            freetext_terms[n] = term
#    # replace placeholders for ID'd terms with canonical term descriptions from all_terms
#    for n,tid in entity_term_ids.iteritems():
#        for term in all_terms:
#            if tid in term:
#                terms[n] = term
#    # replace placeholders for freetext terms
#    for n,term in freetext_terms.iteritems():
#        terms[n] = term
#    # convert unicode terms to str
#    return [str(term) for term in terms]

def tagmanager_process_tags( form_terms ):
    """Formats TagManager tags in format expected by Entity.topics.
    
    TODO This should probably be somewhere else
    
    >>> hidden_terms = u'Topic 1 [94],Topic 2 [95],Topic 3 [244]'
    >>> process_cleaned_terms(hidden_terms, all_terms)
    u'Topic 1 [94]; Topic 2 [95]; Topic 3 [244]'
    """
    return ']; '.join(form_terms.split('],'))


# views ----------------------------------------------------------------


@storage_required
def detail( request, repo, org, cid, eid ):
    collection = Collection.from_json(Collection.collection_path(request,repo,org,cid))
    epath = Entity.entity_path(request,repo,org,cid,eid)
    entity = Entity.from_json(epath)
    tasks = request.session.get('celery-tasks', [])
    return render_to_response(
        'webui/entities/detail.html',
        {'repo': entity.repo,
         'org': entity.org,
         'cid': entity.cid,
         'eid': entity.eid,
         'collection_uid': collection.id,
         'collection': collection,
         'entity': entity,
         'tasks': tasks,
         'unlock_task_id': entity.locked(),},
        context_instance=RequestContext(request, processors=[])
    )

@storage_required
def files( request, repo, org, cid, eid, role ):
    collection = Collection.from_json(Collection.collection_path(request,repo,org,cid))
    entity = Entity.from_json(Entity.entity_path(request,repo,org,cid,eid))
    if role == 'mezzanine':
        files = entity.files_mezzanine()
    else:
        files = entity.files_master()
    duplicates = entity.detect_file_duplicates(role)
    if duplicates:
        url = reverse('webui-entity-files-dedupe', args=[repo,org,cid,eid])
        messages.error(request, 'Duplicate files detected. <a href="%s">More info</a>' % url)
    # paginate
    thispage = request.GET.get('page', 1)
    paginator = Paginator(files, settings.RESULTS_PER_PAGE)
    page = paginator.page(thispage)
    return render_to_response(
        'webui/entities/files.html',
        {'repo': entity.repo,
         'org': entity.org,
         'cid': entity.cid,
         'eid': entity.eid,
         'role': role,
         'collection_uid': collection.id,
         'collection': collection,
         'entity': entity,
         'paginator': paginator,
         'page': page,
         'thispage': thispage,},
        context_instance=RequestContext(request, processors=[])
    )

@storage_required
def addfile_log( request, repo, org, cid, eid ):
    collection = Collection.from_json(Collection.collection_path(request,repo,org,cid))
    entity = Entity.from_json(Entity.entity_path(request,repo,org,cid,eid))
    return render_to_response(
        'webui/entities/addfiles-log.html',
        {'repo': entity.repo,
         'org': entity.org,
         'cid': entity.cid,
         'eid': entity.eid,
         'collection_uid': collection.id,
         'collection': collection,
         'entity': entity,},
        context_instance=RequestContext(request, processors=[])
    )

@storage_required
def changelog( request, repo, org, cid, eid ):
    collection = Collection.from_json(Collection.collection_path(request,repo,org,cid))
    entity = Entity.from_json(Entity.entity_path(request,repo,org,cid,eid))
    return render_to_response(
        'webui/entities/changelog.html',
        {'repo': entity.repo,
         'org': entity.org,
         'cid': entity.cid,
         'eid': entity.eid,
         'collection_uid': collection.id,
         'collection': collection,
         'entity': entity,},
        context_instance=RequestContext(request, processors=[])
    )

@storage_required
def entity_json( request, repo, org, cid, eid ):
    collection = Collection.from_json(Collection.collection_path(request,repo,org,cid))
    entity = Entity.from_json(Entity.entity_path(request,repo,org,cid,eid))
    with open(entity.json_path, 'r') as f:
        json = f.read()
    return HttpResponse(json, content_type="application/json")

@storage_required
def mets_xml( request, repo, org, cid, eid ):
    collection = Collection.from_json(Collection.collection_path(request,repo,org,cid))
    entity = Entity.from_json(Entity.entity_path(request,repo,org,cid,eid))
    soup = BeautifulSoup(entity.mets().xml, 'xml')
    return HttpResponse(soup.prettify(), content_type="application/xml")

@ddrview
@login_required
@storage_required
def new( request, repo, org, cid ):
    """Gets new EID from workbench, creates new entity record.
    
    If it messes up, goes back to collection.
    """
    git_name = request.session.get('git_name')
    git_mail = request.session.get('git_mail')
    if not (git_name and git_mail):
        messages.error(request, WEBUI_MESSAGES['LOGIN_REQUIRED'])
    collection = Collection.from_json(Collection.collection_path(request,repo,org,cid))
    if collection.locked():
        messages.error(request, WEBUI_MESSAGES['VIEWS_COLL_LOCKED'].format(collection.id))
        return HttpResponseRedirect( reverse('webui-collection', args=[repo,org,cid]) )
    collection.repo_fetch()
    if collection.repo_behind():
        messages.error(request, WEBUI_MESSAGES['VIEWS_COLL_BEHIND'].format(collection.id))
        return HttpResponseRedirect( reverse('webui-collection', args=[repo,org,cid]) )
    # get new entity ID
    try:
        entity_ids = api.entities_next(request, repo, org, cid, 1)
    except Exception as e:
        logger.error('Could not get new object ID from workbench!')
        logger.error(str(e.args))
        messages.error(request, WEBUI_MESSAGES['VIEWS_ENT_ERR_NO_IDS'])
        messages.error(request, e)
        return HttpResponseRedirect(reverse('webui-collection', args=[repo,org,cid]))
    eid = int(entity_ids[-1].split('-')[3])
    # create new entity
    entity_uid = '{}-{}-{}-{}'.format(repo,org,cid,eid)
    entity_path = Entity.entity_path(request, repo, org, cid, eid)
    # write entity.json template to entity location
    with open(settings.TEMPLATE_EJSON, 'w') as f:
        f.write(Entity(entity_path).dump_json(template=True))
    # commit files
    exit,status = commands.entity_create(git_name, git_mail,
                                         collection.path, entity_uid,
                                         [collection.json_path_rel, collection.ead_path_rel],
                                         [settings.TEMPLATE_EJSON, settings.TEMPLATE_METS],
                                         agent=settings.AGENT)
    
    # load Entity object, inherit values from parent, write back to file
    entity = Entity.from_json(entity_path)
    entity.inherit(collection)
    entity.write_json()
    updated_files = [entity.json_path]
    exit,status = commands.entity_update(git_name, git_mail,
                                         entity.parent_path, entity.id,
                                         updated_files,
                                         agent=settings.AGENT)
    
    collection.cache_delete()
    if exit:
        logger.error(exit)
        logger.error(status)
        messages.error(request, WEBUI_MESSAGES['ERROR'].format(status))
    else:
        # update search index
        json_path = os.path.join(entity_path, 'entity.json')
        with open(json_path, 'r') as f:
            document = json.loads(f.read())
        docstore.post(settings.DOCSTORE_HOSTS, settings.DOCSTORE_INDEX, document)
        gitstatus_update.apply_async((collection.path,), countdown=2)
        # positive feedback
        return HttpResponseRedirect(reverse('webui-entity-edit', args=[repo,org,cid,eid]))
    # something happened...
    logger.error('Could not create new entity!')
    messages.error(request, WEBUI_MESSAGES['VIEWS_ENT_ERR_CREATE'])
    return HttpResponseRedirect(reverse('webui-collection', args=[repo,org,cid]))

@ddrview
@login_required
@storage_required
def edit( request, repo, org, cid, eid ):
    """
    UI for Entity topics uses TagManager to represent topics as tags,
    and typeahead.js so users only have to type part of a topic.
    """
    git_name = request.session.get('git_name')
    git_mail = request.session.get('git_mail')
    if not git_name and git_mail:
        messages.error(request, WEBUI_MESSAGES['LOGIN_REQUIRED'])
    collection = Collection.from_json(Collection.collection_path(request,repo,org,cid))
    entity = Entity.from_json(Entity.entity_path(request,repo,org,cid,eid))
    if collection.locked():
        messages.error(request, WEBUI_MESSAGES['VIEWS_COLL_LOCKED'].format(collection.id))
        return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
    collection.repo_fetch()
    if collection.repo_behind():
        messages.error(request, WEBUI_MESSAGES['VIEWS_COLL_BEHIND'].format(collection.id))
        return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
    if entity.locked():
        messages.error(request, WEBUI_MESSAGES['VIEWS_ENT_LOCKED'])
        return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
    #
    # load topics choices data
    # TODO This should be baked into models somehow.
    topics_terms = tagmanager_terms('topics')
    facility_terms = tagmanager_terms('facility')
    if request.method == 'POST':
        form = DDRForm(request.POST, fields=ENTITY_FIELDS)
        if form.is_valid():
            
            # clean up after TagManager
            hidden_topics = request.POST.get('hidden-topics', None)
            if hidden_topics:
                form.cleaned_data['topics'] = tagmanager_process_tags(hidden_topics)
            hidden_facility = request.POST.get('hidden-facility', None)
            if hidden_facility:
                form.cleaned_data['facility'] = tagmanager_process_tags(hidden_facility)
            
            entity.form_post(form)
            entity.write_json()
            entity.dump_mets()
            updated_files = [entity.json_path, entity.mets_path,]
            success_msg = WEBUI_MESSAGES['VIEWS_ENT_UPDATED']
            
            # if inheritable fields selected, propagate changes to child objects
            inheritables = entity.selected_inheritables(form.cleaned_data)
            modified_ids,modified_files = entity.update_inheritables(inheritables, form.cleaned_data)
            if modified_files:
                updated_files = updated_files + modified_files
            if modified_ids:
                success_msg = 'Object updated. ' \
                              'The value(s) for <b>%s</b> were applied to <b>%s</b>' % (
                                  ', '.join(inheritables), ', '.join(modified_ids))
            
            exit,status = commands.entity_update(git_name, git_mail,
                                                 entity.parent_path, entity.id,
                                                 updated_files,
                                                 agent=settings.AGENT)
            collection.cache_delete()
            if exit:
                messages.error(request, WEBUI_MESSAGES['ERROR'].format(status))
            else:
                # update search index
                with open(entity.json_path, 'r') as f:
                    document = json.loads(f.read())
                docstore.post(settings.DOCSTORE_HOSTS, settings.DOCSTORE_INDEX, document)
                gitstatus_update.apply_async((collection.path,), countdown=2)
                # positive feedback
                messages.success(request, success_msg)
                return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
    else:
        form = DDRForm(entity.form_prep(), fields=ENTITY_FIELDS)
    
    topics_prefilled = tagmanager_prefilled_terms(entity.topics, topics_terms)
    facility_prefilled = tagmanager_prefilled_terms(entity.facility, facility_terms)
    # selected terms that don't appear in field_terms
    topics_legacy = tagmanager_legacy_terms(entity.topics, topics_terms)
    facility_legacy = tagmanager_legacy_terms(entity.facility, facility_terms)
    return render_to_response(
        'webui/entities/edit-json.html',
        {'repo': entity.repo,
         'org': entity.org,
         'cid': entity.cid,
         'eid': entity.eid,
         'collection_uid': collection.id,
         'collection': collection,
         'entity': entity,
         'form': form,
         # data for TagManager
         'topics_terms': topics_terms,
         'facility_terms': facility_terms,
         'topics_prefilled': topics_prefilled,
         'facility_prefilled': facility_prefilled,
         },
        context_instance=RequestContext(request, processors=[])
    )


def edit_vocab_terms( request, field ):
    terms = []
    for term in vocab_terms(field)['terms']:
        if term.get('path',None):
            t = '%s [%s]' % (term['path'], term['id'])
        else:
            t = '%s [%s]' % (term['title'], term['id'])
        terms.append(t)
    return render_to_response(
        'webui/entities/vocab.html',
        {'terms': terms,},
        context_instance=RequestContext(request, processors=[])
    )

@ddrview
@login_required
@storage_required
def edit_json( request, repo, org, cid, eid ):
    """
    NOTE: will permit editing even if entity is locked!
    (which you need to do sometimes).
    """
    collection = Collection.from_json(Collection.collection_path(request,repo,org,cid))
    entity = Entity.from_json(Entity.entity_path(request,repo,org,cid,eid))
    #if collection.locked():
    #    messages.error(request, WEBUI_MESSAGES['VIEWS_COLL_LOCKED'].format(collection.id))
    #    return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
    #collection.repo_fetch()
    #if collection.repo_behind():
    #    messages.error(request, WEBUI_MESSAGES['VIEWS_COLL_BEHIND'].format(collection.id))
    #    return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
    #if entity.locked():
    #    messages.error(request, WEBUI_MESSAGES['VIEWS_ENT_LOCKED'])
    #    return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
    #
    if request.method == 'POST':
        form = JSONForm(request.POST)
        if form.is_valid():
            git_name = request.session.get('git_name')
            git_mail = request.session.get('git_mail')
            if git_name and git_mail:
                json = form.cleaned_data['json']
                # TODO validate XML
                with open(entity.json_path, 'w') as f:
                    f.write(json)
                
                exit,status = commands.entity_update(git_name, git_mail,
                                                     entity.parent_path, entity.id,
                                                     [entity.json_path],
                                                     agent=settings.AGENT)
                
                collection.cache_delete()
                if exit:
                    messages.error(request, WEBUI_MESSAGES['ERROR'].format(status))
                else:
                    gitstatus_update.apply_async((collection.path,), countdown=2)
                    messages.success(request, WEBUI_MESSAGES['VIEWS_ENT_UPDATED'])
                    return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
            else:
                messages.error(request, WEBUI_MESSAGES['LOGIN_REQUIRED'])
    else:
        with open(entity.json_path, 'r') as f:
            json = f.read()
        form = JSONForm({'json': json,})
    return render_to_response(
        'webui/entities/edit-raw.html',
        {'repo': entity.repo,
         'org': entity.org,
         'cid': entity.cid,
         'eid': entity.eid,
         'collection_uid': entity.parent_uid,
         'entity': entity,
         'form': form,},
        context_instance=RequestContext(request, processors=[])
    )

@ddrview
@login_required
@storage_required
def delete( request, repo, org, cid, eid, confirm=False ):
    """Delete the requested entity from the collection.
    """
    try:
        entity = Entity.from_json(Entity.entity_path(request,repo,org,cid,eid))
    except:
        raise Http404
    collection = Collection.from_json(entity.parent_path)
    if collection.locked():
        messages.error(request, WEBUI_MESSAGES['VIEWS_COLL_LOCKED'].format(collection.id))
        return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
    if entity.locked():
        messages.error(request, WEBUI_MESSAGES['VIEWS_ENT_LOCKED'])
        return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
    git_name = request.session.get('git_name')
    git_mail = request.session.get('git_mail')
    if not git_name and git_mail:
        messages.error(request, WEBUI_MESSAGES['LOGIN_REQUIRED'])
    #
    if request.method == 'POST':
        form = DeleteEntityForm(request.POST)
        if form.is_valid() and form.cleaned_data['confirmed']:
            collection_delete_entity(request,
                                     git_name, git_mail,
                                     collection, entity,
                                     settings.AGENT)
            return HttpResponseRedirect( reverse('webui-collection', args=[repo,org,cid]) )
    else:
        form = DeleteEntityForm()
    return render_to_response(
        'webui/entities/delete.html',
        {'repo': entity.repo,
         'org': entity.org,
         'cid': entity.cid,
         'eid': entity.eid,
         'entity': entity,
         'form': form,
         },
        context_instance=RequestContext(request, processors=[])
    )

@login_required
@storage_required
def files_dedupe( request, repo, org, cid, eid ):
    git_name = request.session.get('git_name')
    git_mail = request.session.get('git_mail')
    if not (git_name and git_mail):
        messages.error(request, WEBUI_MESSAGES['LOGIN_REQUIRED'])
    collection = Collection.from_json(Collection.collection_path(request,repo,org,cid))
    if collection.locked():
        messages.error(request, WEBUI_MESSAGES['VIEWS_COLL_LOCKED'].format(collection.id))
        return HttpResponseRedirect( reverse('webui-collection', args=[repo,org,cid]) )
    entity = Entity.from_json(Entity.entity_path(request,repo,org,cid,eid))
    duplicate_masters = entity.detect_file_duplicates('master')
    duplicate_mezzanines = entity.detect_file_duplicates('mezzanine')
    duplicates = duplicate_masters + duplicate_mezzanines
    if request.method == 'POST':
        form = RmDuplicatesForm(request.POST)
        if form.is_valid() and form.cleaned_data.get('confirmed',None) \
                and (form.cleaned_data['confirmed'] == True):
            # remove duplicates
            entity.rm_file_duplicates()
            # update metadata files
            entity.write_json()
            entity.dump_mets()
            updated_files = [entity.json_path, entity.mets_path,]
            success_msg = WEBUI_MESSAGES['VIEWS_ENT_UPDATED']
            exit,status = commands.entity_update(git_name, git_mail,
                                                 entity.parent_path, entity.id,
                                                 updated_files,
                                                 agent=settings.AGENT)
            collection.cache_delete()
            if exit:
                messages.error(request, WEBUI_MESSAGES['ERROR'].format(status))
            else:
                # update search index
                with open(entity.json_path, 'r') as f:
                    document = json.loads(f.read())
                docstore.post(settings.DOCSTORE_HOSTS, settings.DOCSTORE_INDEX, document)
                gitstatus_update.apply_async((collection.path,), countdown=2)
                # positive feedback
                messages.success(request, success_msg)
                return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
    else:
        data = {}
        form = RmDuplicatesForm()
    return render_to_response(
        'webui/entities/files-dedupe.html',
        {'repo': entity.repo,
         'org': entity.org,
         'cid': entity.cid,
         'eid': entity.eid,
         'collection_uid': collection.id,
         'collection': collection,
         'entity': entity,
         'duplicates': duplicates,
         'form': form,},
        context_instance=RequestContext(request, processors=[])
    )

@ddrview
@login_required
@storage_required
def edit_mets_xml( request, repo, org, cid, eid ):
    """
    on GET
    - reads contents of EAD.xml
    - puts in form, in textarea
    - user edits XML
    on POST
    - write contents of field to EAD.xml
    - commands.update
    """
    collection = Collection.from_json(Collection.collection_path(request,repo,org,cid))
    entity = Entity.from_json(Entity.entity_path(request,repo,org,cid,eid))
    if collection.locked():
        messages.error(request, WEBUI_MESSAGES['VIEWS_COLL_LOCKED'].format(collection.id))
        return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
    collection.repo_fetch()
    if collection.repo_behind():
        messages.error(request, WEBUI_MESSAGES['VIEWS_COLL_BEHIND'].format(collection.id))
        return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
    if entity.locked():
        messages.error(request, WEBUI_MESSAGES['VIEWS_ENT_LOCKED'])
        return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
    #
    if request.method == 'POST':
        form = UpdateForm(request.POST)
        if form.is_valid():
            git_name = request.session.get('git_name')
            git_mail = request.session.get('git_mail')
            if git_name and git_mail:
                xml = form.cleaned_data['xml']
                # TODO validate XML
                with open(entity.mets_path, 'w') as f:
                    f.write(xml)
                
                exit,status = commands.entity_update(git_name, git_mail,
                                                     entity.parent_path, entity.id,
                                                     [entity.mets_path],
                                                     agent=settings.AGENT)
                
                collection.cache_delete()
                if exit:
                    messages.error(request, WEBUI_MESSAGES['ERROR'].format(status))
                else:
                    messages.success(request, WEBUI_MESSAGES['VIEWS_ENT_UPDATED'])
                    return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
            else:
                messages.error(request, WEBUI_MESSAGES['LOGIN_REQUIRED'])
    else:
        form = UpdateForm({'xml': entity.mets().xml,})
    return render_to_response(
        'webui/entities/edit-mets.html',
        {'repo': entity.repo,
         'org': entity.org,
         'cid': entity.cid,
         'eid': entity.eid,
         'collection_uid': entity.parent_uid,
         'entity': entity,
         'form': form,},
        context_instance=RequestContext(request, processors=[])
    )

@ddrview
@login_required
@storage_required
def edit_xml( request, repo, org, cid, eid, slug, Form, FIELDS, namespaces=None ):
    """Edit the contents of <archdesc>.
    """
    git_name = request.session.get('git_name')
    git_mail = request.session.get('git_mail')
    if not git_name and git_mail:
        messages.error(request, WEBUI_MESSAGES['LOGIN_REQUIRED'])
    collection_uid = '{}-{}-{}'.format(repo, org, cid)
    entity_uid     = '{}-{}-{}-{}'.format(repo, org, cid, eid)
    collection_abs = os.path.join(settings.MEDIA_BASE, collection_uid)
    entity_abs     = os.path.join(collection_abs,'files',entity_uid)
    entity_rel     = os.path.join('files',entity_uid)
    xml_path_rel   = 'mets.xml'
    xml_path_abs   = os.path.join(entity_abs, xml_path_rel)
    with open(xml_path_abs, 'r') as f:
        xml = f.read()
    fields = Form.prep_fields(FIELDS, xml, namespaces=namespaces)
    #
    if request.method == 'POST':
        form = Form(request.POST, fields=fields, namespaces=namespaces)
        if form.is_valid():
            form_fields = form.fields
            cleaned_data = form.cleaned_data
            xml_new = Form.process(xml, fields, form, namespaces=namespaces)
            # TODO validate XML
            with open(xml_path_abs, 'w') as fnew:
                fnew.write(xml_new)
            # TODO validate XML
            exit,status = commands.entity_update(git_name, git_mail,
                                                 collection_abs, entity_uid, [xml_path_rel],
                                                 agent=settings.AGENT)
            if exit:
                messages.error(request, WEBUI_MESSAGES['ERROR'].format(status))
            else:
                messages.success(request, '<{}> updated'.format(slug))
                return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
    else:
        form = Form(fields=fields, namespaces=namespaces)
    # template
    try:
        tf = 'webui/collections/edit-{}.html'.format(slug)
        t = get_template(tf)
        template_filename = tf
    except:
        template_filename = 'webui/entities/edit-xml.html'
    return render_to_response(
        template_filename,
        {'repo': repo,
         'org': org,
         'cid': cid,
         'eid': eid,
         'collection_uid': collection_uid,
         'entity_uid': entity_uid,
         'slug': slug,
         'form': form,},
        context_instance=RequestContext(request, processors=[])
    )

def edit_mets( request, repo, org, cid, eid ):
    return edit_xml(request, repo, org, cid, eid,
                    slug='mets',
                    Form=MetsForm, FIELDS=METS_FIELDS,
                    namespaces=NAMESPACES,)

@ddrview
@login_required
@storage_required
def unlock( request, repo, org, cid, eid, task_id ):
    """Provides a way to remove entity lockfile through the web UI.
    """
    git_name = request.session.get('git_name')
    git_mail = request.session.get('git_mail')
    if not git_name and git_mail:
        messages.error(request, WEBUI_MESSAGES['LOGIN_REQUIRED'])
    collection = Collection.from_json(Collection.collection_path(request,repo,org,cid))
    entity = Entity.from_json(Entity.entity_path(request,repo,org,cid,eid))
    if task_id and entity.locked() and (task_id == entity.locked()):
        entity.unlock(task_id)
        messages.success(request, 'Object <b>%s</b> unlocked.' % entity.id)
    return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
