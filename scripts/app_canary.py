"""Run in an app container with its existing SDK and Django settings.

stdin JSON: {"marker": "...", "dsn": "optional temporary override"}.
No real requests, database writes or broker jobs: the Django view and Celery
task exist only in this process; the task runs eagerly with an in-memory broker.
"""
import json
import logging
import os
import sys
import types
from urllib.parse import urlsplit

config = json.load(sys.stdin)
if config.get('dsn'):
    os.environ['SENTRY_DSN'] = config['dsn']
import django
django.setup()
import sentry_sdk
from django.test import Client, override_settings
from django.urls import path
from celery import Celery

marker = config['marker']
captured = []
client = sentry_sdk.get_client()
original_before_send = client.options.get('before_send')

def before_send(event, hint):
    if original_before_send:
        event = original_before_send(event, hint)
    if event:
        captured.append(event['event_id'])
    return event

client.options['before_send'] = before_send
sentry_sdk.set_tag('errex_migration_canary', marker)

def broken_view(request):
    raise RuntimeError('Errex Django canary ' + marker)

routes = types.ModuleType('errex_canary_urls')
routes.urlpatterns = [path('errex-canary/', broken_view)]
with override_settings(ROOT_URLCONF=routes, MIDDLEWARE=[], ALLOWED_HOSTS=['testserver']):
    response = Client(raise_request_exception=False).get('/errex-canary/')
    assert response.status_code == 500

try:
    from tenant_schemas_celery.task import TenantTask
    task_class = TenantTask
except ImportError:
    from celery import Task
    task_class = Task
app = Celery('errex_canary', broker='memory://', backend='cache+memory://', task_cls=task_class)
app.conf.task_always_eager = True

@app.task(name='errex.synthetic_canary', ignore_result=True)
def broken_task():
    raise RuntimeError('Errex Celery canary ' + marker)

assert broken_task.apply(throw=False).failed()
logging.getLogger('errex.canary').error('Errex logging canary %s', marker)
sentry_sdk.flush(timeout=15)
dsn = urlsplit(client.options['dsn'])
print(json.dumps({'marker': marker, 'event_ids': captured, 'host': dsn.hostname, 'port': dsn.port, 'project': dsn.path}))
