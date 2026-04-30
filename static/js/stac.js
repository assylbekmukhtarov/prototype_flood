const STAC_URL = "https://earth-search.aws.element84.com/v1";
const COLLECTION = "sentinel-2-l2a";

async function searchBestScene(bbox, dateStart, dateEnd) {
    const url = `${STAC_URL}/search`;
    const body = {
        collections: [COLLECTION],
        bbox: bbox,
        datetime: `${dateStart}T00:00:00Z/${dateEnd}T23:59:59Z`,
        query: { "eo:cloud_cover": { lt: 20 } },
        limit: 20,
    };

    const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });

    if (!res.ok) throw new Error(`STAC error: ${res.status}`);
    const data = await res.json();

    const items = data.features || [];
    if (items.length === 0) return null;

    items.sort((a, b) =>
        (a.properties["eo:cloud_cover"] ?? 100) - (b.properties["eo:cloud_cover"] ?? 100)
    );

    return items[0];
}
