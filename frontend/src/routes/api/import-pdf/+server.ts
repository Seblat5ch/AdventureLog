const PUBLIC_SERVER_URL = process.env['PUBLIC_SERVER_URL'];
const endpoint = PUBLIC_SERVER_URL || 'http://localhost:8000';
import { json } from '@sveltejs/kit';

/** Handle PDF upload — forwards to Django backend */
export async function POST({ request, cookies }) {
	const sessionid = cookies.get('sessionid') || '';

	try {
		const body = await request.arrayBuffer();
		const contentType = request.headers.get('content-type') || '';

		const response = await globalThis.fetch(`${endpoint}/api/import-pdf/`, {
			method: 'POST',
			headers: {
				'Content-Type': contentType,
				'Cookie': sessionid ? `sessionid=${sessionid}` : ''
			},
			body: body
		});

		const responseText = await response.text();
		return new Response(responseText, {
			status: response.status,
			headers: { 'Content-Type': response.headers.get('content-type') || 'application/json' }
		});
	} catch (error) {
		console.error('PDF import proxy error:', error);
		return json({ error: `Proxy error: ${error}` }, { status: 500 });
	}
}
