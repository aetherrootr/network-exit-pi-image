"""Optional sing-box configuration and validation."""
import secrets
import subprocess
import components

def private_proxy(listen):
    if listen != '127.0.0.1':
        raise ValueError('The generated proxy listens on localhost; import a reviewed configuration for other listeners')
    return {'log': {'level': 'info', 'timestamp': True},
            'inbounds': [{'type': 'mixed', 'listen': listen, 'listen_port': 1080,
                          'users': [{'username': 'proxy', 'password': secrets.token_urlsafe(24)}]}],
            'outbounds': [{'type': 'direct', 'tag': 'broadband'}],
            'route': {'final': 'broadband'}}



def validate():
    subprocess.run(['sing-box','check','-c',str(components.CONFIG/'sing-box.json')],check=True,capture_output=True)
