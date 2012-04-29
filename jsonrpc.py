""" jsonrpc.py

    Implements a JSON-RPC v1.1 service
"""
import sys
import inspect
import urllib2
from django.utils import simplejson
from django.http import HttpResponse

class JsonRpcError(Exception):
    pass

class ServiceBase(object):

    def __init__(self, auth_func=None, show_exceptions=False):
        self.methods = {}
        self.auth_func = auth_func
        self.show_exceptions = show_exceptions

    def to_json(self, data):
        return simplejson.dumps(data)

    def get_response(self, id, result):
        return {
            'version': '1.1',
            'id': id,
            'result': result,
            'error': None,
        }

    def get_error(self, id, code, message):
        return {
            'id': id,
            'version': '1.1',
            'error': {
                'name': 'JsonRpcError',
                'code': code,
                'message': message,
            },
        }

    def add_method(self, method_name, method):
        self.methods[method_name] = method

    def is_authorized(self, request, method_name, params):

        if self.auth_func:
            auth = self.auth_func(request, method_name, params)

            try:
                auth, msg = auth
            except (ValueError, TypeError):
                msg = 'method "%s" does not exist' % method_name

            return auth, msg

        return True, ''

    def process_request(self, request):
        try:
            data = simplejson.loads(request.raw_post_data)
            id, method_name, params = data['id'], data['method'], data['params']
        except:
            # Doing a blanket except here
            error = self.get_error(id, 100, 'Malformed JSON-RPC 1.1 request')
            return self.to_json(error)

        auth, auth_msg = self.is_authorized(request, method_name, params)
        if not auth:
            error = self.get_error(id, 100, auth_msg)
            return self.to_json(error)

        method = self.get_method(method_name)
        if not method:
            error = self.get_error(id, 100, 'method "%s" does not exist' % method_name)
            return self.to_json(error)

        try:
            result = method(request, *params)
            response = self.get_response(id, result)

        except Exception:
            if self.show_exceptions:
                etype, evalue, etb = sys.exc_info()
                response = self.get_error(id, 100, '%s: %s' %(etype.__name__, evalue))
            else:
                response = self.get_error(id, 100, 'An error occurred')

        return self.to_json(response)

    def list_methods(self):
        return self.methods.keys()

    def get_method(self, method_name):
        try:
            return self.methods[method_name]
        except KeyError:
            return None

    def get_smd(self, url):
        smd = {
            'serviceType': 'JSON-RPC',
            'serviceURL': url,
            'methods': []
        }

        for method_name in self.list_methods():
            sig = inspect.getargspec(self.get_method(method_name))
            smd['methods'].append({
                'name': method_name,
                'parameters': [ {'name': val} for val in sig.args if \
                    val not in ('self', 'request') ]
            })

        return self.to_json(smd)

class Service(ServiceBase):

    def __call__(self, request):

        # JSON-RPC method calls come in as POSTs
        if request.method == 'POST':
            return HttpResponse(self.process_request(request), mimetype='application/json')

        url = request.get_full_path()
        return HttpResponse(self.get_smd(url), mimetype='application/json')

def servicemethod(service, name=None):

    def wrapped(method):
        if isinstance(service, Service):
            service.add_method(name or method.__name__, method)
        else:
            emsg = 'Service "%s" not found' % service.__name__
            raise NotImplementedError, emsg
        return method

    return wrapped

class ServiceProxy(object):

    def __init__(self, url):
        self.smd_url = url
        self.service_url = url
        self.smd = {}
        self.methods = []
        self.call_id = 0

    def to_json(self, data):
        return simplejson.dumps(data)

    def from_json(self, data):
        return simplejson.loads(data)

    def get_smd(self, url=None):
        data = urllib2.urlopen(url or self.smd_url)
        self.smd = self.from_json(data.read())
        data.close()
        self.methods += [method['name'] for method in self.smd['methods']]

    def call_method(self, method_name, params):
        self.call_id += 1
        data = {
            'method': method_name,
            'params': params,
            'id': self.call_id,
        }
        print data
        data = urllib2.urlopen(self.service_url, self.to_json(data))
        resp = self.from_json(data.read())
        data.close()
        return resp

    def __getattr__(self, attr):

        if not self.smd:
            self.get_smd()

        def wrapped(*args):
            resp = self.call_method(attr, args)
            try:
                return resp['result']
            except KeyError:
                err_name, err_msg = resp['error']['name'], resp['error']['message']
                raise JsonRpcError, '%s: %s' % (err_name, err_msg)

        return wrapped

def proxymethod(serviceproxy, name=None):

    def wrapped(method):
        def wrapped2(*args):
            result = getattr(serviceproxy, name or method.__name__)(*args)
            return method(result)
        return wrapped2
    return wrapped
