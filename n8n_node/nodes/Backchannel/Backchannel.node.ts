import {
  IDataObject,
  IExecuteFunctions,
  INodeExecutionData,
  INodeType,
  INodeTypeDescription,
  NodeOperationError,
} from 'n8n-workflow';

/**
 * Backchannel n8n node.
 *
 * Operations:
 *   - postTask         POST /v1/channels/{name}/messages on a claimable channel.
 *   - claimTask        Drain the channel, claim the first unclaimed message.
 *   - broadcast        POST a message on a broadcast channel.
 *   - subscribe        GET /v1/channels/{name}/messages?since={cursor}.
 *   - ack              POST /v1/messages/{id}/ack with a given actor.
 *
 * All operations idempotency-key the writes via the per-execution UUID so
 * retries from n8n's own retry policy do not double-process.
 */
export class Backchannel implements INodeType {
  description: INodeTypeDescription = {
    displayName: 'Backchannel',
    name: 'backchannel',
    icon: 'file:backchannel.svg',
    group: ['transform'],
    version: 1,
    description: 'Hand work to (or claim work from) another agent over Backchannel.',
    defaults: { name: 'Backchannel' },
    inputs: ['main'],
    outputs: ['main'],
    credentials: [{ name: 'backchannelApi', required: true }],
    properties: [
      {
        displayName: 'Operation',
        name: 'operation',
        type: 'options',
        noDataExpression: true,
        options: [
          { name: 'Post Task', value: 'postTask', description: 'Hand a task to another agent (claimable channel).' },
          { name: 'Claim Task', value: 'claimTask', description: 'Claim the next available task on a channel.' },
          { name: 'Broadcast', value: 'broadcast', description: 'Send a fan-out message on a broadcast channel.' },
          { name: 'Subscribe', value: 'subscribe', description: 'Read recent messages on a channel.' },
          { name: 'Ack', value: 'ack', description: 'Acknowledge a claimed task is done.' },
        ],
        default: 'postTask',
      },
      {
        displayName: 'Channel',
        name: 'channel',
        type: 'string',
        default: '',
        required: true,
        description: 'Channel name or id.',
        displayOptions: { hide: { operation: ['ack'] } },
      },
      {
        displayName: 'Content',
        name: 'content',
        type: 'string',
        typeOptions: { rows: 3 },
        default: '',
        description: 'Task payload (plain text or JSON string).',
        displayOptions: { show: { operation: ['postTask', 'broadcast'] } },
      },
      {
        displayName: 'Actor Label',
        name: 'actorLabel',
        type: 'string',
        default: 'n8n',
        description: 'A short label identifying this workflow as the producer.',
        displayOptions: { show: { operation: ['postTask', 'broadcast'] } },
      },
      {
        displayName: 'Actor',
        name: 'actor',
        type: 'string',
        default: 'n8n-worker',
        description: 'Worker identity used when claiming or acking. Resolved (and created if new) by name, scoped to your API key.',
        displayOptions: { show: { operation: ['claimTask', 'ack'] } },
      },
      {
        displayName: 'Message ID',
        name: 'messageId',
        type: 'string',
        default: '',
        description: 'Message id (returned by Post Task or Claim Task).',
        displayOptions: { show: { operation: ['ack'] } },
      },
      {
        displayName: 'Since Cursor',
        name: 'since',
        type: 'string',
        default: '',
        description: 'Pass next_cursor from a previous Subscribe call to page forward. Leave empty for newest.',
        displayOptions: { show: { operation: ['subscribe'] } },
      },
      {
        displayName: 'Limit',
        name: 'limit',
        type: 'number',
        default: 50,
        description: 'Max messages per page.',
        displayOptions: { show: { operation: ['subscribe'] } },
      },
    ],
  };

  async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
    const items = this.getInputData();
    const out: INodeExecutionData[] = [];
    const creds = (await this.getCredentials('backchannelApi')) as { baseUrl: string; apiKey: string };
    const baseUrl = (creds.baseUrl || 'https://backchannel.oakstack.eu').replace(/\/$/, '');

    const callApi = async (
      method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
      path: string,
      body?: IDataObject,
      qs?: IDataObject,
    ): Promise<any> => {
      const idemKey = `n8n-${this.getExecutionId()}-${method}-${path}`;
      return this.helpers.httpRequestWithAuthentication.call(this, 'backchannelApi', {
        method,
        url: `${baseUrl}${path}`,
        json: true,
        body,
        qs,
        headers: { 'Idempotency-Key': idemKey },
      });
    };

    const ensureChannel = async (name: string, mode: 'claimable' | 'broadcast'): Promise<string> => {
      try {
        const created = await callApi('POST', '/v1/channels', { name, mode });
        return created.id || name;
      } catch (err: any) {
        if (err.httpCode === 409) return name; // exists
        throw err;
      }
    };

    for (let i = 0; i < items.length; i++) {
      const operation = this.getNodeParameter('operation', i) as string;
      try {
        if (operation === 'postTask') {
          const channel = this.getNodeParameter('channel', i) as string;
          const content = this.getNodeParameter('content', i) as string;
          const actorLabel = this.getNodeParameter('actorLabel', i) as string;
          const channelId = await ensureChannel(channel, 'claimable');
          const env = await callApi('POST', `/v1/channels/${encodeURIComponent(channelId)}/messages`, {
            content,
            actor_label: actorLabel,
          });
          out.push({ json: { channel: channelId, ...env } });
        } else if (operation === 'broadcast') {
          const channel = this.getNodeParameter('channel', i) as string;
          const content = this.getNodeParameter('content', i) as string;
          const actorLabel = this.getNodeParameter('actorLabel', i) as string;
          const channelId = await ensureChannel(channel, 'broadcast');
          const env = await callApi('POST', `/v1/channels/${encodeURIComponent(channelId)}/messages`, {
            content,
            actor_label: actorLabel,
          });
          out.push({ json: { channel: channelId, ...env } });
        } else if (operation === 'claimTask') {
          const channel = this.getNodeParameter('channel', i) as string;
          const actor = this.getNodeParameter('actor', i) as string;
          // Let the server return only unclaimed messages; the actor name is
          // resolved (and created if new) owner-scoped by the claim endpoint.
          const page = await callApi('GET', `/v1/channels/${encodeURIComponent(channel)}/messages`, undefined, {
            limit: 20,
            status: 'unclaimed',
          });
          let claimed: any = null;
          for (const msg of page.data || []) {
            try {
              claimed = await callApi('POST', `/v1/messages/${msg.id}/claim`, { actor });
              break;
            } catch (err: any) {
              if (err.httpCode === 409) continue; // raced — someone else claimed it
              throw err;
            }
          }
          out.push({ json: claimed ?? { claimed: null, note: 'no unclaimed messages available' } });
        } else if (operation === 'subscribe') {
          const channel = this.getNodeParameter('channel', i) as string;
          const since = this.getNodeParameter('since', i, '') as string;
          const limit = this.getNodeParameter('limit', i, 50) as number;
          const qs: IDataObject = { limit };
          if (since) qs.since = since;
          const page = await callApi('GET', `/v1/channels/${encodeURIComponent(channel)}/messages`, undefined, qs);
          out.push({ json: page });
        } else if (operation === 'ack') {
          const messageId = this.getNodeParameter('messageId', i) as string;
          const actor = this.getNodeParameter('actor', i) as string;
          const r = await callApi('POST', `/v1/messages/${messageId}/ack`, { actor });
          out.push({ json: r });
        } else {
          throw new NodeOperationError(this.getNode(), `Unsupported operation: ${operation}`);
        }
      } catch (err) {
        if (this.continueOnFail()) {
          out.push({ json: { error: (err as Error).message } });
          continue;
        }
        throw err;
      }
    }
    return [out];
  }
}
