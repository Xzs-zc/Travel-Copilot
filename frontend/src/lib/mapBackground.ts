const STORAGE_KEY = "traveling:map-background";

const MAP_BACKGROUNDS = [
  "/backgrounds/map-1.jpg",
  "/backgrounds/map-2.jpg",
  "/backgrounds/map-3.jpg",
  "/backgrounds/map-4.jpg",
];

export function getRandomMapBackground() {
  return MAP_BACKGROUNDS[Math.floor(Math.random() * MAP_BACKGROUNDS.length)];
}

export function getInitialMapBackground() {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY) || getRandomMapBackground();
  } catch {
    return getRandomMapBackground();
  }
}

export function rememberMapBackground(url: string) {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, url);
  } catch {
    // Storage can be unavailable in private or restricted browsing modes.
  }
}

export function preloadMapBackground(url: string) {
  return new Promise<void>((resolve, reject) => {
    const image = new Image();
    image.decoding = "async";
    image.onload = () => resolve();
    image.onerror = () => reject(new Error(`Unable to load map background: ${url}`));
    image.src = url;
  });
}
