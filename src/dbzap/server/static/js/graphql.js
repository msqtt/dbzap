// GraphQL introspection helpers
import { apiFetch } from './api.js';

const INTROSPECTION_QUERY = `
query IntrospectionQuery {
  __schema {
    queryType { fields { name description args { name type { kind name ofType { kind name } } } } }
    mutationType { fields { name description args { name type { kind name ofType { kind name } } } } }
  }
}`;

export async function introspect() {
  try {
    const { resp } = await apiFetch('/graphql', {
      method: 'POST',
      body: { query: INTROSPECTION_QUERY },
    });
    if (!resp.ok) return null;
    const json = await resp.json();
    return json.data?.__schema ?? null;
  } catch { return null; }
}

export function buildQueryFields(args) {
  if (!args || args.length === 0) return '';
  return args.map(a => `${a.name}: $${a.name}`).join(', ');
}

export function unwrapType(t) {
  if (!t) return 'String';
  if (t.name) return t.name;
  return unwrapType(t.ofType);
}
