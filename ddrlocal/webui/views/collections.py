import os

from bs4 import BeautifulSoup

from django.conf import settings
from django.contrib import messages
from django.core.context_processors import csrf
from django.core.files import File
from django.core.urlresolvers import reverse
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import Http404, get_object_or_404, render_to_response
from django.template import RequestContext

from DDR import commands

from webui.forms.collections import NewCollectionForm, UpdateForm
from webui.views.decorators import login_required, storage_required


# helpers --------------------------------------------------------------

def collection_gitweb_url(collection_uid):
    """Returns gitweb URL for collection.
    """
    return '{}?p={}.git'.format(settings.GITWEB_URL, collection_uid)


def collection_entities(soup):
    """Given a BeautifulSoup-ified EAD doc, get list of entity UIDs
    
    <dsc>
      <head>
       Inventory
      </head>
      <c01>
       <did>
        <unittitle eid="ddr-testing-201304081359-1">
         Entity description goes here
        </unittitle>
       </did>
      </c01>
      <c01>
       <did>
        <unittitle eid="ddr-testing-201304081359-2">
         Entity description goes here
        </unittitle>
       </did>
      </c01>
     </dsc>
    """
    entities = []
    for tag in soup.find_all('unittitle'):
        e = tag['eid'].split('-')
        repo,org,cid,eid = e[0],e[1],e[2],e[3]
        entities.append( {'uid': tag['eid'],
                          'repo': repo,
                          'org': org,
                          'cid': cid,
                          'eid': eid,
                          'title': tag.string.strip(),} )
    return entities



# views ----------------------------------------------------------------

@storage_required
def collections( request ):
    collections = []
    repo = None
    org = None
    colls = commands.collections_local(settings.DDR_BASE_PATH,
                                       settings.DDR_REPOSITORY,
                                       settings.DDR_ORGANIZATION)
    for coll in colls:
        if coll:
            coll = os.path.basename(coll)
            c = coll.split('-')
            repo,org,cid = c[0],c[1],c[2]
            collections.append( (coll,repo,org,cid) )
    return render_to_response(
        'webui/collections/index.html',
        {'repo': repo,
         'org': org,
         'collections': collections,},
        context_instance=RequestContext(request, processors=[])
    )

@storage_required
def collection( request, repo, org, cid ):
    collection_uid = '{}-{}-{}'.format(repo, org, cid)
    collection_path = os.path.join(settings.DDR_BASE_PATH, collection_uid)
    #
    exit,status = commands.status(collection_path)
    #exit,astatus = commands.annex_status(collection_path)
    #
    ead = open( os.path.join(collection_path, 'ead.xml'), 'r').read()
    ead_soup = BeautifulSoup(ead)
    #
    changelog = open( os.path.join(collection_path, 'changelog'), 'r').read()
    #
    entities = collection_entities(ead_soup)
    return render_to_response(
        'webui/collections/collection.html',
        {'repo': repo,
         'org': org,
         'cid': cid,
         'collection_uid': collection_uid,
         'status': status,
         #'astatus': astatus,
         'ead': ead,
         'changelog': changelog,
         'entities': entities,
         'gitweb_url': collection_gitweb_url(collection_uid),},
        context_instance=RequestContext(request, processors=[])
    )

@login_required
@storage_required
def collection_sync( request, repo, org, cid ):
    collection_uid = '{}-{}-{}'.format(repo, org, cid)
    collection_path = os.path.join(settings.DDR_BASE_PATH, collection_uid)
    #
    if request.method == 'POST':
        git_name = request.session.get('git_name')
        git_mail = request.session.get('git_mail')
        messages.info(request, git_name)
        messages.info(request, git_mail)
        if git_name and git_mail:
            exit,status = commands.sync(git_name, git_mail, collection_path)
            #
            if exit:
                messages.error(request, 'Error: {}'.format(status))
            else:
                messages.success(request, 'Collection synced with server: {}'.format(status))
        else:
            messages.error(request, 'Login is required')
    return HttpResponseRedirect( reverse('webui-collection', args=[repo,org,cid]) )

@login_required
@storage_required
def collection_new( request ):
    """
    TODO webui.views.collections.collection_new: get new CID from workbench
    """
    if request.method == 'POST':
        form = NewCollectionForm(request.POST)
        if form.is_valid():
            git_name = request.session.get('git_name')
            git_mail = request.session.get('git_mail')
            messages.info(request, git_name)
            messages.info(request, git_mail)
            if git_name and git_mail:
                repo = form.cleaned_data['repo']
                org = form.cleaned_data['org']
                cid = form.cleaned_data['cid']
                collection_uid = '{}-{}-{}'.format(repo,org,cid)
                collection_path = os.path.join(settings.DDR_BASE_PATH, collection_uid)
                messages.info(request, collection_uid)
                messages.info(request, collection_path)
                
                exit,status = commands.create(git_name, git_mail, collection_path)
                
                if exit:
                    messages.error(request, 'Error: {}'.format(status))
                else:
                    messages.success(request, 'New collection created: {}'.format(status))
                    return HttpResponseRedirect( reverse('webui-collection', args=[repo,org,cid]) )
            else:
                messages.error(request, 'Login is required')
    else:
        form = NewCollectionForm()
    return render_to_response(
        'webui/collections/collection-new.html',
        {'form': form,},
        context_instance=RequestContext(request, processors=[])
    )

@login_required
@storage_required
def collection_update( request, repo, org, cid ):
    """
    on GET
    - reads contents of EAD.xml
    - puts in form, in textarea
    - user edits XML
    on POST
    - write contents of field to EAD.xml
    - commands.update
    """
    collection_uid = '{}-{}-{}'.format(repo, org, cid)
    collection_path = os.path.join(settings.DDR_BASE_PATH, collection_uid)
    ead_path_rel = 'ead.xml'
    ead_path_abs = os.path.join(collection_path, ead_path_rel)
    #
    if request.method == 'POST':
        form = UpdateForm(request.POST)
        if form.is_valid():
            git_name = request.session.get('git_name')
            git_mail = request.session.get('git_mail')
            if git_name and git_mail:
                xml = form.cleaned_data['xml']
                # TODO validate XML
                with open(ead_path_abs, 'w') as f:
                    f.write(xml)
                
                exit,status = commands.update(git_name, git_mail, collection_path, [ead_path_rel])
                
                if exit:
                    messages.error(request, 'Error: {}'.format(status))
                else:
                    messages.success(request, 'Collection metadata updated')
                    return HttpResponseRedirect( reverse('webui-collection', args=[repo,org,cid]) )
            else:
                messages.error(request, 'Login is required')
    else:
        with open(ead_path_abs, 'r') as f:
            xml = f.read()
        form = UpdateForm({'xml':xml,})
    return render_to_response(
        'webui/collections/collection-update.html',
        {'repo': repo,
         'org': org,
         'cid': cid,
         'collection_uid': collection_uid,
         'form': form,},
        context_instance=RequestContext(request, processors=[])
    )