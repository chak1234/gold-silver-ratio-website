/** Fetch JSON from the site's own API, throwing on any non-success status. */
export async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`${url} returned ${response.status}`);
  }
  return response.json();
}
