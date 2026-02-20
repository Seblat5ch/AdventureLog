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

	// Get the user's session cookie for authentication
	const sessionid = cookies.get('sessionid') || '';

	try {
		const body = await request.arrayBuffer();
		const contentType = request.headers.get('content-type') || '';

		// Forward Cognito OIDC headers if present (for SSO auth)
		const headers: Record<string, string> = {
			'Content-Type': contentType,
			'X-CSRFToken': csrfToken,
			Cookie: `csrftoken=${csrfToken}; sessionid=${sessionid}`
		};

		// Pass through ALB Cognito headers for the middleware
		const oidcData = request.headers.get('x-amzn-oidc-data');
		if (oidcData) headers['x-amzn-oidc-data'] = oidcData;
		const oidcIdentity = request.headers.get('x-amzn-oidc-identity');
		if (oidcIdentity) headers['x-amzn-oidc-identity'] = oidcIdentity;
		const oidcToken = request.headers.get('x-amzn-oidc-accesstoken');
		if (oidcToken) headers['x-amzn-oidc-accesstoken'] = oidcToken;

		const response = await fetch(`${endpoint}/api/import-pdf/`, {
			method: 'POST',
			headers,
			body: body
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
