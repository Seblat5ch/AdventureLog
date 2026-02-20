const PUBLIC_SERVER_URL = process.env['PUBLIC_SERVER_URL'];
const endpoint = PUBLIC_SERVER_URL || 'http://localhost:8000';
import { fetchCSRFToken } from '$lib/index.server';
import { json } from '@sveltejs/kit';

/** Handle PDF upload — forwards the multipart body to Django backend */
export async function POST({ request, cookies }) {
	const csrfToken = await fetchCSRFToken();
	if (!csrfToken) {
		return json({ error: 'CSRF token is missing' }, { status: 400 });
	}

	const sessionid = cookies.get('sessionid') || '';
	if (!sessionid) {
		return json({ error: 'Not authenticated. Please log in first.' }, { status: 401 });
	}

	try {
		const body = await request.arrayBuffer();
		const contentType = request.headers.get('content-type') || '';

		// Use native fetch (not SvelteKit's event.fetch) to call the backend directly
		const response = await globalThis.fetch(`${endpoint}/api/import-pdf/`, {
			method: 'POST',
			headers: {
				'Content-Type': contentType,
				'X-CSRFToken': csrfToken,
				'Cookie': `csrftoken=${csrfToken}; sessionid=${sessionid}`
			},
			body: body
		});

		const responseData = await response.text();

		return new Response(responseData, {
			status: response.status,
			headers: { 'Content-Type': response.headers.get('content-type') || 'application/json' }
		});
	} catch (error) {
		console.error('Error forwarding PDF import:', error);
		return json({ error: 'Internal Server Error' }, { status: 500 });
	}
}
