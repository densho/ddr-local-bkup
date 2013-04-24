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

from webui.forms.entities import NewEntityForm, UpdateForm, AddFileForm
from webui.views.decorators import login_required, storage_required


# helpers --------------------------------------------------------------

def entity_files(soup, collection_abs, entity_rel):
    """Given a BeautifulSoup-ified METS doc, get list of entity files
    
    ...
    <fileSec>
     <fileGrp USE="master">
      <file CHECKSUM="fadfbcd8ceb71b9cfc765b9710db8c2c" CHECKSUMTYPE="md5">
       <Flocat href="files/6a00e55055.png"/>
      </file>
     </fileGrp>
     <fileGrp USE="master">
      <file CHECKSUM="42d55eb5ac104c86655b3382213deef1" CHECKSUMTYPE="md5">
       <Flocat href="files/20121205.jpg"/>
      </file>
     </fileGrp>
    </fileSec>
    ...
    """
    files = []
    for tag in soup.find_all('flocat'):
        cid = os.path.basename(collection_abs)
        f = {
            'abs': os.path.join(collection_abs, entity_rel, tag['href']),
            'name': os.path.join(cid, entity_rel, tag['href']),
            'basename': os.path.basename(tag['href']),
            'size': 1234567,
        }
        files.append(f)
    return files

def handle_uploaded_file(f, dest_dir):
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)
    print('dest_dir {}'.format(dest_dir))
    dest_path_abs = os.path.join(dest_dir, f.name)
    print('dest_path_abs {}'.format(dest_path_abs))
    with open(dest_path_abs, 'wb+') as destination:
        for chunk in f.chunks():
            destination.write(chunk)
    print('destination {}'.format(destination))
    return dest_path_abs


# views ----------------------------------------------------------------

@storage_required
def entity( request, repo, org, cid, eid ):
    collection_uid = '{}-{}-{}'.format(repo, org, cid)
    entity_uid     = '{}-{}-{}-{}'.format(repo, org, cid, eid)
    collection_abs = os.path.join(settings.DDR_BASE_PATH, collection_uid)
    entity_abs     = os.path.join(collection_abs,'files',entity_uid)
    entity_rel     = os.path.join('files',entity_uid)
    #
    mets = open( os.path.join(entity_abs, 'mets.xml'), 'r').read()
    mets_soup = BeautifulSoup(mets)
    #
    changelog = open( os.path.join(entity_abs, 'changelog'), 'r').read()
    #
    files = entity_files(mets_soup, collection_abs, entity_rel)
    return render_to_response(
        'webui/entities/entity.html',
        {'repo': repo,
         'org': org,
         'cid': cid,
         'eid': eid,
         'collection_uid': collection_uid,
         'entity_uid': entity_uid,
         'collection_path': collection_abs,
         'entity_path': entity_abs,
         'mets': mets,
         'changelog': changelog,
         'files': files,},
        context_instance=RequestContext(request, processors=[])
    )

@login_required
@storage_required
def entity_new( request, repo, org, cid ):
    """
    TODO webui.views.entities.entity_new: get new EID from workbench
    """
    if request.method == 'POST':
        form = NewEntityForm(request.POST)
        if form.is_valid():
            git_name = request.session.get('git_name')
            git_mail = request.session.get('git_mail')
            messages.info(request, git_name)
            messages.info(request, git_mail)
            if git_name and git_mail:
                eid = form.cleaned_data['eid']
                collection_uid = '{}-{}-{}'.format(repo,org,cid)
                collection_path = os.path.join(settings.DDR_BASE_PATH, collection_uid)
                entity_uid = '{}-{}-{}-{}'.format(repo,org,cid,eid)
                messages.info(request, collection_uid)
                messages.info(request, collection_path)
                
                exit,status = commands.entity_create(git_name, git_mail, collection_path, entity_uid)
                
                if exit:
                    messages.error(request, 'Error: {}'.format(status))
                else:
                    messages.success(request, 'New entity created: {}'.format(status))
                    return HttpResponseRedirect( reverse('webui-collection', args=[repo,org,cid]) )
            else:
                messages.error(request, 'Login is required')
    else:
        form = NewEntityForm()
    return render_to_response(
        'webui/entities/entity-new.html',
        {'repo': repo,
         'org': org,
         'cid': cid,
         'form': form,},
        context_instance=RequestContext(request, processors=[])
    )

@login_required
@storage_required
def entity_update( request, repo, org, cid, eid ):
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
    entity_uid     = '{}-{}-{}-{}'.format(repo, org, cid, eid)
    collection_abs = os.path.join(settings.DDR_BASE_PATH, collection_uid)
    entity_abs     = os.path.join(collection_abs,'files',entity_uid)
    entity_rel     = os.path.join('files',entity_uid)
    xml_path_rel   = 'mets.xml'
    xml_path_abs   = os.path.join(entity_abs, xml_path_rel)
    #
    if request.method == 'POST':
        form = UpdateForm(request.POST)
        if form.is_valid():
            git_name = request.session.get('git_name')
            git_mail = request.session.get('git_mail')
            if git_name and git_mail:
                xml = form.cleaned_data['xml']
                # TODO validate XML
                with open(xml_path_abs, 'w') as f:
                    f.write(xml)
                
                exit,status = commands.entity_update(git_name, git_mail, collection_abs, entity_uid, [xml_path_rel])
                
                if exit:
                    messages.error(request, 'Error: {}'.format(status))
                else:
                    messages.success(request, 'Entity updated')
                    return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
            else:
                messages.error(request, 'Login is required')
    else:
        with open(xml_path_abs, 'r') as f:
            xml = f.read()
        form = UpdateForm({'xml':xml,})
    return render_to_response(
        'webui/entities/entity-update.html',
        {'repo': repo,
         'org': org,
         'cid': cid,
         'eid': eid,
         'collection_uid': collection_uid,
         'entity_uid': entity_uid,
         'form': form,},
        context_instance=RequestContext(request, processors=[])
    )

@login_required
@storage_required
def entity_add( request, repo, org, cid, eid ):
    """Add an entity to collection
    """
    collection_uid = '{}-{}-{}'.format(repo, org, cid)
    entity_uid     = '{}-{}-{}-{}'.format(repo, org, cid, eid)
    collection_abs = os.path.join(settings.DDR_BASE_PATH, collection_uid)
    entity_abs     = os.path.join(collection_abs,'files',entity_uid)
    entity_rel     = os.path.join('files',entity_uid)
    entity_files_dir = os.path.join(entity_abs, 'files')
    messages.debug(request, 'entity_files_dir: {}'.format(entity_files_dir))
    #
    if request.method == 'POST':
        form = AddFileForm(request.POST, request.FILES)
        if form.is_valid():
            git_name = request.session.get('git_name')
            git_mail = request.session.get('git_mail')
            if git_name and git_mail:
                role = form.cleaned_data['role']
                # write file to entity files dir
                file_abs = handle_uploaded_file(request.FILES['file'], entity_files_dir)
                file_rel = os.path.basename(file_abs)
                
                exit,status = commands.entity_annex_add(git_name, git_mail, collection_abs, entity_uid, file_rel)
                
                if exit:
                    messages.error(request, 'Error: {}'.format(status))
                else:
                    messages.success(request, 'New file added: {}'.format(status))
                    return HttpResponseRedirect( reverse('webui-entity', args=[repo,org,cid,eid]) )
            else:
                messages.error(request, 'Login is required')
    else:
        form = AddFileForm()
    return render_to_response(
        'webui/entities/entity-add.html',
        {'repo': repo,
         'org': org,
         'cid': cid,
         'eid': eid,
         'collection_uid': collection_uid,
         'entity_uid': entity_uid,
         'form': form,
     },
        context_instance=RequestContext(request, processors=[])
    )

@login_required
@storage_required
def entity_file( request, repo, org, cid, eid, filenum ):
    """Add file to entity.
    """
    filenum = int(filenum)
    collection_uid = '{}-{}-{}'.format(repo, org, cid)
    entity_uid     = '{}-{}-{}-{}'.format(repo, org, cid, eid)
    collection_abs = os.path.join(settings.DDR_BASE_PATH, collection_uid)
    entity_abs     = os.path.join(collection_abs,'files',entity_uid)
    entity_rel     = os.path.join('files',entity_uid)
    #
    mets = open( os.path.join(entity_abs, 'mets.xml'), 'r').read()
    mets_soup = BeautifulSoup(mets)
    #
    changelog = open( os.path.join(entity_abs, 'changelog'), 'r').read()
    #
    files = entity_files(mets_soup, collection_abs, entity_rel)
    return render_to_response(
        'webui/entities/entity-file.html',
        {'repo': repo,
         'org': org,
         'cid': cid,
         'eid': eid,
         'collection_uid': collection_uid,
         'entity_uid': entity_uid,
         'collection_path': collection_abs,
         'entity_path': entity_abs,
         'mets': mets,
         'changelog': changelog,
         'files': files,
         'file': files[filenum],},
        context_instance=RequestContext(request, processors=[])
    )