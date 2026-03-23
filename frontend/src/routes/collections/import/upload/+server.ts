import { json } from '@sveltejs/kit';
import { fetchCSRFToken } from '$lib/index.server';
import type { RequestHandler } from './$types';

const PUBLIC_SERVER_URL = process.env['PUBLIC_SERVER_URL'];
const endpoint = PUBLIC_SERVER_URL || 'http://localhost:8000';

export const POST: RequestHandler = async ({ request, cookies }) => {
	const csrfToken = await fetchCSRFToken();
	if (!csrfToken) {
		return json({ error: 'CSRF token is missing' }, { status: 400 });
	}

	const originalCookie = request.headers.get('cookie') || '';
	const filteredCookies = originalCookie
		.split(';')
		.map((c: string) => c.trim())
		.filter((c: string) => c && !c.startsWith('csrftoken='))
		.join('; ');
	const cookieHeader = filteredCookies
		? `${filteredCookies}; csrftoken=${csrfToken}`
		: `csrftoken=${csrfToken}`;

	const body = await request.arrayBuffer();
	const headers = new Headers(request.headers);

	const response = await fetch(`${endpoint}/api/import-pdf/`, {
		method: 'POST',
		headers: {
			...Object.fromEntries(headers),
			'X-CSRFToken': csrfToken,
			Cookie: cookieHeader,
		},
		body,
	});

	const data = await response.arrayBuffer();
	return new Response(data, {
		status: response.status,
		headers: { 'Content-Type': 'application/json' },
	});
};

export const GET: RequestHandler = async ({ url, request, cookies }) => {
	const taskId = url.searchParams.get('task_id');
	const collectionId = url.searchParams.get('collection_id');

	const csrfToken = await fetchCSRFToken();
	const originalCookie = request.headers.get('cookie') || '';
	const filteredCookies = originalCookie
		.split(';')
		.map((c: string) => c.trim())
		.filter((c: string) => c && !c.startsWith('csrftoken='))
		.join('; ');
	const cookieHeader = filteredCookies
		? `${filteredCookies}; csrftoken=${csrfToken}`
		: `csrftoken=${csrfToken}`;

	// Check collection generation status
	if (collectionId) {
		const response = await fetch(`${endpoint}/api/import-pdf/collection/${collectionId}/status/`, {
			headers: {
				...Object.fromEntries(new Headers(request.headers)),
				'X-CSRFToken': csrfToken || '',
				Cookie: cookieHeader,
			},
		});
		const data = await response.arrayBuffer();
		return new Response(data, {
			status: response.status,
			headers: { 'Content-Type': 'application/json' },
		});
	}

	// Poll task status
	if (!taskId) {
		return json({ error: 'Missing task_id or collection_id' }, { status: 400 });
	}

	const response = await fetch(`${endpoint}/api/import-pdf/${taskId}/`, {
		headers: {
			...Object.fromEntries(new Headers(request.headers)),
			'X-CSRFToken': csrfToken || '',
			Cookie: cookieHeader,
		},
	});

	const data = await response.arrayBuffer();
	return new Response(data, {
		status: response.status,
		headers: { 'Content-Type': 'application/json' },
	});
};

export const PUT: RequestHandler = async ({ request, cookies }) => {
	const csrfToken = await fetchCSRFToken();
	if (!csrfToken) {
		return json({ error: 'CSRF token is missing' }, { status: 400 });
	}

	const originalCookie = request.headers.get('cookie') || '';
	const filteredCookies = originalCookie
		.split(';')
		.map((c: string) => c.trim())
		.filter((c: string) => c && !c.startsWith('csrftoken='))
		.join('; ');
	const cookieHeader = filteredCookies
		? `${filteredCookies}; csrftoken=${csrfToken}`
		: `csrftoken=${csrfToken}`;

	const body = await request.json();
	const collectionId = body.collection_id;
	if (!collectionId) {
		return json({ error: 'Missing collection_id' }, { status: 400 });
	}

	const response = await fetch(`${endpoint}/api/import-pdf/collection/${collectionId}/regenerate/`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			'X-CSRFToken': csrfToken,
			Cookie: cookieHeader,
		},
		body: JSON.stringify({}),
	});

	const data = await response.arrayBuffer();
	return new Response(data, {
		status: response.status,
		headers: { 'Content-Type': 'application/json' },
	});
};
