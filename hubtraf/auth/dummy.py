import time


async def login_dummy(session, hub_url, log, username, password):
    """
    Log in username with password to hub_url in aiohttp session.

    log is used to emit timing and status information.
    """
    start_time = time.monotonic()

    url = hub_url / 'hub/login'

    try:
        resp = await session.post(url, data={'username': username, 'password': password}, allow_redirects=False)
    except Exception as e:
        log.msg('Login: Failed with exception {}'.format(repr(e)), action='login', phase='failed', duration=time.monotonic() - start_time)
        return None
    if resp.status != 302:
        log.msg('Login: Failed with response {}'.format(str(resp)), action='login', phase='failed', duration=time.monotonic() - start_time)
        return None

    try:
        resp = await session.get(url)
    except Exception as e:
        log.msg('Login: Get failed {}'.format(repr(e)), action='username-check', phase='failed', duration=time.monotonic() - start_time)
        return None
    
    # Extract (possibly normalized) username from hub
    if resp.url.parts[-1] == 'tree':
        return resp.url.parts[-2]
    
    return resp.url.parts[-1]
