from enum import Enum, auto
import aiohttp
import socket
import uuid
import random
from yarl import URL
import asyncio
import async_timeout
import nbformat
import structlog
import time
import colorama

logger = structlog.get_logger()


class User:
    class States(Enum):
        CLEAR = 1
        LOGGED_IN = 2
        SERVER_STARTED = 3
        KERNEL_STARTED = 4

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.session.close()

    def __init__(self, username, hub_url, login_handler):
        """
        A simulated JupyterHub user.

        username - name of the user.
        hub_url - base url of the hub.
        login_handler - a awaitable callable that will be passed the following parameters:
                            username
                            session (aiohttp session object)
                            log (structlog log object)
                            hub_url (yarl URL object)

                        It should 'log in' the user with whatever requests it needs to
                        perform. If no uncaught exception is thrown, login is considered
                        a success.

                        Usually a partial of a generic function is passed in here.
        """
        self.username = username
        self.hub_url = URL(hub_url)

        self.state = User.States.CLEAR
        self.notebook_url = self.hub_url / 'user' / self.username

        self.log = logger.bind(
            username=username
        )
        self.login_handler = login_handler
        self.headers = {
            'Referer': str(self.hub_url / 'hub/')
        }

    def update_username(self, username):
        self.log = logger.bind(
            username = username
        )
        self.username = username
        self.notebook_url = self.hub_url / 'user' / username
        #self.notebook_url = self.hub_url / 'user' / username

    def success(self, kind, **kwargs):
        kwargs_pretty = " ".join([f"{k}:{v}" for k, v in kwargs.items()])
        print(f'{colorama.Fore.GREEN}Success:{colorama.Style.RESET_ALL}', kind, self.username,  kwargs_pretty)

    def failure(self, kind, **kwargs):
        kwargs_pretty = " ".join([f"{k}:{v}" for k, v in kwargs.items()])
        print(f'{colorama.Fore.RED}Failure:{colorama.Style.RESET_ALL}', kind, self.username,  kwargs_pretty)

    def debug(self, kind, **kwargs):
        kwargs_pretty = " ".join([f"{k}:{v}" for k, v in kwargs.items()])
        print(f'{colorama.Fore.YELLOW}Debug:{colorama.Style.RESET_ALL}', kind, self.username,  kwargs_pretty)

    async def login(self):
        """
        Log in to the JupyterHub.

        We only log in, and try to not start the server itself. This
        makes our testing code simpler, but we need to be aware of the fact this
        might cause differences vs how users normally use this.
        """
        # We only log in if we haven't done anything already!
        assert self.state == User.States.CLEAR

        start_time = time.monotonic()
        username = await self.login_handler(log=self.log, hub_url=self.hub_url, session=self.session, username=self.username)
        if not username:
            return False
        self.update_username(username)
        hub_cookie = self.session.cookie_jar.filter_cookies(self.hub_url).get('hub', None)
        if hub_cookie:
            self.log = self.log.bind(hub=hub_cookie.value)
        self.success('login', duration=time.monotonic() - start_time)
        self.state = User.States.LOGGED_IN
        return True

    async def ensure_server_api(self, timeout=300, spawn_refresh_time=30):
        api_url = self.hub_url / 'hub/api'
        self.headers['Authorization'] = 'token 16c7709a19b04f77a11bc6f4b88a9080'

        async def server_running():
            async with self.session.get(api_url / 'users' / self.username, headers=self.headers) as resp:
                userinfo = await resp.json()
                server = userinfo.get('servers', {}).get('', {})
                self.debug('server-start', phase='waiting', ready=server.get('ready'), pending=server.get('pending'))
                return server.get('ready', False)


        self.debug('server-start', phase='start')
        start_time = time.monotonic()

        async with self.session.post(api_url / 'users' / self.username / 'server', headers=self.headers) as resp:
            if resp.status == 201:
                # Server created
                # FIXME: Verify this server is actually up
                self.success('server-start', duration=time.monotonic() - start_time)
                self.state = User.States.SERVER_STARTED
                return True
            elif resp.status == 202:
                # Server start request received, not necessarily started
                # FIXME: Verify somehow?
                self.debug('server-start', phase='waiting')
                while not (await server_running()):
                    await asyncio.sleep(0.5)
                self.success('server-start', duration=time.monotonic() - start_time)
                self.state = User.States.SERVER_STARTED
                return True
            elif resp.status == 400:
                body = await resp.json()
                if body['message'] == f'{self.username} is already running':
                    self.state = User.States.SERVER_STARTED
                    return True
            print(await resp.json())
            print(resp.request_info)
            return False


    async def ensure_server_simulate(self, timeout=300, spawn_refresh_time=30):
        assert self.state == User.States.LOGGED_IN

        start_time = time.monotonic()
        self.debug('server-start', phase='start')
        i = 0
        while True:
            i += 1
            self.debug('server-start', phase='attempt-start', attempt=i + 1)
            try:
                resp = await self.session.get(self.hub_url / 'hub/spawn')
            except Exception as e:
                self.debug('server-start', exception=str(e), attempt=i + 1, phase='attempt-failed', duration=time.monotonic() - start_time)
                continue
            # Check if paths match, ignoring query string (primarily, redirects=N), fragments
            target_url_tree = self.notebook_url / 'tree'
            if resp.url.scheme == target_url_tree.scheme and resp.url.host == target_url_tree.host and resp.url.path == target_url_tree.path:
                self.success('server-start', phase='complete', attempt=i + 1, duration=time.monotonic() - start_time)
                break
            target_url_lab = self.notebook_url / 'lab'
            if resp.url.scheme == target_url_lab.scheme and resp.url.host == target_url_lab.host and resp.url.path == target_url_lab.path:
                self.success('server-start', phase='complete', attempt=i + 1, duration=time.monotonic() - start_time)
                break
            if time.monotonic() - start_time >= timeout:
                self.failure('server-start', phase='failed', duration=time.monotonic() - start_time, reason='timeout')
                return False
            # Always log retries, so we can count 'in-progress' actions
            self.debug('server-start', resp=str(resp), phase='attempt-complete', duration=time.monotonic() - start_time, attempt=i + 1)
            # FIXME: Add jitter?
            await asyncio.sleep(random.uniform(0, spawn_refresh_time))

        self.state = User.States.SERVER_STARTED
        self.headers['X-XSRFToken'] = self.xsrf_token
        return True

    async def stop_server(self):
        assert self.state == User.States.SERVER_STARTED
        self.debug('server-stop', phase='start')
        start_time = time.monotonic()
        try:
            resp = await self.session.delete(
                self.hub_url / 'hub/api/users' / self.username / 'server',
                headers=self.headers
            )
        except Exception as e:
            self.failure('server-stop', exception=str(e), duration=time.monotonic() - start_time)
            return False
        if resp.status != 202 and resp.status != 204:
            self.failure('server-stop', exception=str(resp), duration=time.monotonic() - start_time)
            return False
        self.success('server-stop', duration=time.monotonic() - start_time)
        self.state = User.States.LOGGED_IN
        return True

    async def start_kernel(self):
        assert self.state == User.States.SERVER_STARTED

        self.debug('kernel-start', phase='start')
        start_time = time.monotonic()

        try:
            resp = await self.session.post(self.notebook_url / 'api/kernels', headers=self.headers)
        except Exception as e:
            self.failure('kernel-start', exception=str(e), duration=time.monotonic() - start_time)
            return False

        if resp.status != 201:
            self.failure('kernel-start', exception=str(resp), duration=time.monotonic() - start_time)
            return False
        self.kernel_id = (await resp.json())['id']
        self.success('kernel-start', duration=time.monotonic() - start_time)
        self.state = User.States.KERNEL_STARTED
        return True

    @property
    def xsrf_token(self):
        notebook_cookies = self.session.cookie_jar.filter_cookies(self.notebook_url)
        assert '_xsrf' in notebook_cookies
        xsrf_token = notebook_cookies['_xsrf'].value
        return xsrf_token

    async def stop_kernel(self):
        assert self.state == User.States.KERNEL_STARTED

        self.debug('kernel-stop', phase='start')
        start_time = time.monotonic()
        try:
            resp = await self.session.delete(self.notebook_url / 'api/kernels' / self.kernel_id, headers=self.headers)
        except Exception as e:
            self.failure('kernel-stop', exception=str(e), duration=time.monotonic() - start_time)
            return False

        if resp.status != 204:
            self.failure('kernel-stop', exception=str(resp), duration=time.monotonic() - start_time)
            return False

        self.success('kernel-stop', duration=time.monotonic() - start_time)
        self.state = User.States.SERVER_STARTED
        return True

    def request_execute_code(self, msg_id, code):
        return {
            "header": {
                "msg_id": msg_id,
                "username": self.username,
                "msg_type": "execute_request",
                "version": "5.2"
            },
            "metadata": {},
            "content": {
                "code": code,
                "silent": False,
                "store_history": True,
                "user_expressions": {},
                "allow_stdin": True,
                "stop_on_error": True
            },
            "buffers": [],
            "parent_header": {},
            "channel": "shell"
        }

    async def assert_code_output(self, code, outputs, ws):

        self.debug('code-execute', phase='start')

        exec_start_time = time.monotonic()
        msg_id = str(uuid.uuid4())
        input_acknowledged = False

        await ws.send_json(self.request_execute_code(msg_id, code))
        self.debug('code-execute source: ' + code)

        async for msg_text in ws:
            if msg_text.type != aiohttp.WSMsgType.TEXT:
                self.failure(
                    'code-execute',
                    iteration=iteration,
                    message=str(msg_text),
                    duration=time.monotonic() - exec_start_time
                )
                return False

            msg = msg_text.json()

            if 'parent_header' in msg and msg['parent_header'].get('msg_id') == msg_id:
                if msg['channel'] == 'iopub':
                    response = None
                    if msg['msg_type'] in ('pyin', 'execute_input'):
                        input_acknowledged = True
                    elif msg['msg_type'] == 'status':
                        if msg['content']['execution_state'] == 'idle' and (len(outputs) == 0) and input_acknowledged:
                            # No output recorded for some cells so break
                            self.debug('code-execute idle')
                            duration = time.monotonic() - exec_start_time
                            break
                    elif msg['msg_type'] == 'execute_result':
                        reference = outputs.pop(0)['data']['text/plain']
                        response = msg['content']['data']['text/plain']
                        duration = time.monotonic() - exec_start_time
                    elif msg['msg_type'] == 'stream':
                        reference = outputs.pop(0)['text']
                        response = msg['content']['text']
                    else:
                        self.debug('code-execute unknown msg: ' + msg['msg_type'])
                        duration = time.monotonic() - exec_start_time
                        break
                            
                    if response:
                        assert response == reference
                        self.debug('code-execute validated')
                        duration = time.monotonic() - exec_start_time
                        break

        self.success('code-execute', duration=duration)
        return True


    async def assert_notebook_output(self, notebook, execute_timeout, repeat_time_seconds=None):

        nb = nbformat.read(notebook, as_version=4) 
        code_cells = [cell for cell in nb.cells if cell['cell_type'] == 'code']

        channel_url = self.notebook_url / 'api/kernels' / self.kernel_id / 'channels'
        self.debug('kernel-connect', phase='start')
        is_connected = False

        try:
            async with self.session.ws_connect(channel_url) as ws:
                is_connected = True
                self.debug('kernel-connect', phase='complete')
                start_time = time.monotonic()
                iteration = 0

                for code_cell in code_cells:
                    await self.assert_code_output(
                            code_cell['source'], 
                            code_cell['outputs'],  
                            ws)

        except Exception as e:
            self.failure('notebook-execute', exception=str(e))
            return False


