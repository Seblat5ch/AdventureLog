<script lang="ts">
	import { goto } from '$app/navigation';
	import { addToast } from '$lib/toasts';
	import { t } from 'svelte-i18n';

	let isDragging = false;
	let isUploading = false;
	let uploadProgress = '';
	let selectedFile: File | null = null;

	function handleDragOver(e: DragEvent) {
		e.preventDefault();
		isDragging = true;
	}

	function handleDragLeave() {
		isDragging = false;
	}

	function handleDrop(e: DragEvent) {
		e.preventDefault();
		isDragging = false;
		const files = e.dataTransfer?.files;
		if (files && files.length > 0) {
			const file = files[0];
			if (file.type === 'application/pdf') {
				selectedFile = file;
			} else {
				addToast('error', 'Please drop a PDF file.');
			}
		}
	}

	function handleFileSelect(e: Event) {
		const input = e.target as HTMLInputElement;
		if (input.files && input.files.length > 0) {
			selectedFile = input.files[0];
		}
	}

	async function pollTaskStatus(taskId: string) {
		const maxAttempts = 60; // 60 * 3s = 3 minutes max
		for (let i = 0; i < maxAttempts; i++) {
			await new Promise((r) => setTimeout(r, 3000));

			try {
				const res = await fetch(`/api/import-pdf/${taskId}/`);
				if (!res.ok) {
					addToast('error', 'Failed to check import status.');
					return;
				}
				const data = await res.json();

				if (data.status === 'done' && data.collection) {
					addToast('success', `Trip "${data.collection.name}" created!`);
					goto(`/collections/${data.collection.id}`);
					return;
				} else if (data.status === 'error') {
					addToast('error', data.error || 'AI agent failed.');
					return;
				}

				// Still running — update progress
				if (data.status === 'running') {
					uploadProgress = 'AI is parsing your itinerary...';
				}
			} catch {
				// Network blip, keep polling
			}
		}
		addToast('error', 'Import timed out. Check your collections — it may still complete.');
	}

	async function uploadPdf() {
		if (!selectedFile) return;

		isUploading = true;
		uploadProgress = 'Uploading PDF...';

		try {
			const formData = new FormData();
			formData.append('pdf', selectedFile);

			const res = await fetch('/api/import-pdf/', {
				method: 'POST',
				body: formData
			});

			if (res.ok || res.status === 202) {
				const data = await res.json();
				if (data.task_id) {
					uploadProgress = 'AI is generating your itinerary...';
					await pollTaskStatus(data.task_id);
				} else if (data.id) {
					// Fallback: synchronous response (shouldn't happen but just in case)
					addToast('success', `Trip "${data.name}" created!`);
					goto(`/collections/${data.id}`);
				}
			} else {
				const err = await res.json();
				addToast('error', err.error || 'Failed to import PDF.');
			}
		} catch (e) {
			addToast('error', 'Network error. Please try again.');
		} finally {
			isUploading = false;
			uploadProgress = '';
		}
	}
</script>

<svelte:head>
	<title>Import Travel PDF</title>
</svelte:head>

<div class="container mx-auto max-w-2xl px-4 py-8">
	<h1 class="text-3xl font-bold mb-2">Import Travel PDF</h1>
	<p class="text-base-content/70 mb-8">
		Drop a travel itinerary PDF and AI will create a complete trip with locations, hotels,
		flights, and notes.
	</p>

	<!-- svelte-ignore a11y-no-static-element-interactions -->
	<div
		class="border-2 border-dashed rounded-xl p-12 text-center transition-colors cursor-pointer
			{isDragging ? 'border-primary bg-primary/10' : 'border-base-300 hover:border-primary/50'}"
		on:dragover={handleDragOver}
		on:dragleave={handleDragLeave}
		on:drop={handleDrop}
		on:click={() => document.getElementById('pdf-input')?.click()}
		role="button"
		tabindex="0"
		on:keydown={(e) => { if (e.key === 'Enter') document.getElementById('pdf-input')?.click(); }}
	>
		{#if isUploading}
			<div class="flex flex-col items-center gap-4">
				<span class="loading loading-spinner loading-lg text-primary"></span>
				<p class="text-lg font-medium">{uploadProgress}</p>
				<p class="text-sm text-base-content/50">This may take up to a minute...</p>
			</div>
		{:else if selectedFile}
			<div class="flex flex-col items-center gap-4">
				<svg xmlns="http://www.w3.org/2000/svg" class="h-16 w-16 text-success" fill="none" viewBox="0 0 24 24" stroke="currentColor">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
				</svg>
				<p class="text-lg font-medium">{selectedFile.name}</p>
				<p class="text-sm text-base-content/50">{(selectedFile.size / 1024).toFixed(1)} KB</p>
				<button class="btn btn-primary btn-lg mt-2" on:click|stopPropagation={uploadPdf}>
					Generate Itinerary
				</button>
				<button class="btn btn-ghost btn-sm" on:click|stopPropagation={() => (selectedFile = null)}>
					Choose different file
				</button>
			</div>
		{:else}
			<div class="flex flex-col items-center gap-4">
				<svg xmlns="http://www.w3.org/2000/svg" class="h-16 w-16 text-base-content/30" fill="none" viewBox="0 0 24 24" stroke="currentColor">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
				</svg>
				<p class="text-lg font-medium">Drop your travel PDF here</p>
				<p class="text-sm text-base-content/50">or click to browse</p>
			</div>
		{/if}

		<input
			id="pdf-input"
			type="file"
			accept=".pdf"
			class="hidden"
			on:change={handleFileSelect}
		/>
	</div>

	<div class="mt-8 text-sm text-base-content/50">
		<p class="font-medium mb-2">What the AI extracts:</p>
		<ul class="list-disc list-inside space-y-1">
			<li>Trip dates and name</li>
			<li>Destinations with map coordinates</li>
			<li>Flights, buses, and transfers</li>
			<li>Hotels and lodges with check-in/out dates</li>
			<li>Travel tips and notes</li>
			<li>Packing checklists</li>
		</ul>
	</div>
</div>
