export async function onRequest(context) {
  const { params, env } = context;
  const path = params.path ? params.path.join('/') : '';
  
  if (!path) {
    return new Response('Not found', { status: 404 });
  }

  try {
    const object = await env.BUCKET.get(path);
    
    if (!object) {
      return new Response('File not found', { status: 404 });
    }

    const headers = new Headers();
    headers.set('Content-Type', 'application/pdf');
    headers.set('Content-Disposition', 'inline; filename="' + path.split('/').pop() + '"');
    headers.set('Cache-Control', 'private, max-age=3600');

    return new Response(object.body, { headers });
  } catch (e) {
    return new Response('Error: ' + e.message, { status: 500 });
  }
}
