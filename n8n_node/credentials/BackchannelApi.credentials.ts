import {
  IAuthenticateGeneric,
  ICredentialTestRequest,
  ICredentialType,
  INodeProperties,
} from 'n8n-workflow';

export class BackchannelApi implements ICredentialType {
  name = 'backchannelApi';
  displayName = 'Backchannel API';
  documentationUrl = 'https://backchannel.oakstack.eu/agent-guide';
  properties: INodeProperties[] = [
    {
      displayName: 'Base URL',
      name: 'baseUrl',
      type: 'string',
      default: 'https://backchannel.oakstack.eu',
      description:
        'Defaults to the public shared sandbox (backchannel.oakstack.eu) — rate-limited, for trying the protocol. Point this at your own self-hosted instance for production.',
    },
    {
      displayName: 'API Key',
      name: 'apiKey',
      type: 'string',
      typeOptions: { password: true },
      default: '',
      placeholder: 'bck_xxxx.xxxxxxxxxx',
      description:
        "Get one in 60 seconds — no signup — with: curl -X POST {baseUrl}/v1/keys -H 'Content-Type: application/json' -d '{\"agent_label\":\"n8n\"}'",
    },
  ];

  authenticate: IAuthenticateGeneric = {
    type: 'generic',
    properties: {
      headers: {
        'X-API-Key': '={{ $credentials.apiKey }}',
      },
    },
  };

  test: ICredentialTestRequest = {
    request: {
      baseURL: '={{ $credentials.baseUrl }}',
      url: '/v1/keys/me',
      method: 'GET',
    },
  };
}
