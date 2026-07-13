export function randomUnit() {
  const value = new Uint32Array(1);
  crypto.getRandomValues(value);
  return value[0] / 0xffffffff;
}

function randomChoice(values) {
  return values[Math.floor(randomUnit() * values.length) % values.length];
}

export function createGeometricAvatar() {
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 256;
  const context = canvas.getContext("2d", { alpha: false });
  const palette = ["#fa2a97", "#ff9900", "#f7815e", "#dff34e", "#61e37a", "#7779ff", "#638cff"];
  const background = context.createLinearGradient(0, 0, 256, 256);
  background.addColorStop(0, "#171238");
  background.addColorStop(1, "#29205e");
  context.fillStyle = background;
  context.fillRect(0, 0, 256, 256);

  context.globalAlpha = 0.9;
  for (let index = 0; index < 8; index += 1) {
    const x = randomUnit() * 256;
    const y = randomUnit() * 256;
    const size = 36 + randomUnit() * 92;
    context.fillStyle = randomChoice(palette);
    context.beginPath();
    if (index % 3 === 0) {
      context.arc(x, y, size / 2, 0, Math.PI * 2);
    } else if (index % 3 === 1) {
      context.moveTo(x, y - size / 2);
      context.lineTo(x + size / 2, y + size / 2);
      context.lineTo(x - size / 2, y + size / 2);
      context.closePath();
    } else {
      context.rect(x - size / 2, y - size / 2, size, size);
    }
    context.fill();
  }

  const sheen = context.createLinearGradient(0, 0, 0, 256);
  sheen.addColorStop(0, "rgba(255,255,255,.18)");
  sheen.addColorStop(0.45, "rgba(255,255,255,0)");
  sheen.addColorStop(1, "rgba(5,3,20,.25)");
  context.fillStyle = sheen;
  context.fillRect(0, 0, 256, 256);
  return canvas;
}

export function canvasBlob(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob(blob => {
      if (blob) resolve(blob);
      else reject(new Error("The browser could not create a profile picture."));
    }, "image/png");
  });
}

async function decodeImage(file) {
  if ("createImageBitmap" in window) return createImageBitmap(file);
  const objectUrl = URL.createObjectURL(file);
  const image = new Image();
  image.src = objectUrl;
  try {
    await image.decode();
    return image;
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

export async function canvasFromFile(file) {
  const acceptedTypes = new Set(["image/png", "image/jpeg", "image/webp"]);
  if (!acceptedTypes.has(file.type)) throw new Error("Choose a PNG, JPEG, or WebP image.");
  if (file.size > 10 * 1024 * 1024) throw new Error("Profile pictures must be 10 MB or smaller.");
  const image = await decodeImage(file);
  const width = image.width || image.naturalWidth;
  const height = image.height || image.naturalHeight;
  if (!width || !height) throw new Error("The selected image could not be read.");
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 256;
  const context = canvas.getContext("2d", { alpha: false });
  const sourceSize = Math.min(width, height);
  context.drawImage(
    image,
    (width - sourceSize) / 2,
    (height - sourceSize) / 2,
    sourceSize,
    sourceSize,
    0,
    0,
    256,
    256
  );
  if (typeof image.close === "function") image.close();
  return canvas;
}
