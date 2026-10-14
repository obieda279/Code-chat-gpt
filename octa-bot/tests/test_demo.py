import asyncio
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile
import unittest
import aiohttp


class DemoTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_cli_demo_can_login_and_save(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",0))
            port=sock.getsockname()[1]
        with tempfile.TemporaryDirectory() as temp:
            env=dict(os.environ)
            env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])+os.pathsep+env.get('PYTHONPATH','')
            process=await asyncio.create_subprocess_exec(sys.executable,'-m','sentinel','--demo','--port',str(port),cwd=temp,env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            base=f'http://127.0.0.1:{port}'
            try:
                async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                    for _ in range(100):
                        try:
                            async with client.get(base+'/healthz') as response:
                                self.assertTrue((await response.json())['demo'])
                                break
                        except aiohttp.ClientConnectionError:
                            await asyncio.sleep(.05)
                    else:
                        self.fail('Demo HTTP server did not start')
                    async with client.get(base+'/auth/login') as response:self.assertEqual(response.status,200)
                    async with client.get(base+'/api/me') as response:me=await response.json()
                    async with client.get(base+'/api/guilds') as response:gid=(await response.json())[0]['id']
                    async with client.get(base+f'/api/guilds/{gid}/config') as response:record=await response.json()
                    record['config']['timezone']='Europe/Madrid'
                    async with client.put(base+f'/api/guilds/{gid}/config',json={'config':record['config'],'version':record['version']},headers={'Origin':base,'X-CSRF-Token':me['csrf']}) as response:
                        self.assertEqual(response.status,200,await response.text())
                        self.assertEqual((await response.json())['config']['timezone'],'Europe/Madrid')
            finally:
                if process.returncode is None:process.terminate()
                try:await asyncio.wait_for(process.communicate(),5)
                except asyncio.TimeoutError:
                    process.kill();await process.communicate()


if __name__=='__main__':unittest.main()
