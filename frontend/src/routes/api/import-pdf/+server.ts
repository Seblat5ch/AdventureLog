const PUBLIC_SERVER_URL = process.env['PUBLIC_SERVER_URL'];
const endpoint = PUBLIC_SERVER_URL || 'http://localhost:8000';
import { fetchCSRFToken } from '$lib/index.server';
import { json } from '@sveltejs/kit';

/** Handle PDF upload — forwards the multipart body as-is (binary safe) */
export async function POST({ request, fetch, cookies }) {
	const csrfToken = await fetchCSRFToken();
	if (!csrfToken) {
		return json({ error: 'CSRF token is missing' }, { status: 400 });
	}

	const cookieHeader = `csrftoken=${csrfToken}; Path=/; HttpOnly; SameSite=Lax`;

	try {
		// Forward the raw request body (multipart/form-data) without converting to text
		const body = await request.arrayBuffer();
		const contentType = request.headers.get('content-type') || '';

		const response = await fetch(`${endpoint}/api/import-pdf/`, {
			method: 'POST',
			headers: {
				'Content-Type': contentType,
				'X-CSRFToken': csrfToken,
				Cookie: cookieHeader
			},
			body: body,
			credentials: 'include'
		});

		const responseData = await response.arrayBuffer();
		const cleanHeaders = new Headers(response.headers);
		cleanHeaders.delete('set-cookie');

		return new Response(responseData, {
			status: response.status,
			headers: cleanHeaders
		});
	} catch (error) {
		console.error('Error forwarding PDF import:', error);
		return json({ error: 'Internal Server Error' }, { status: 500 });
	}
}
