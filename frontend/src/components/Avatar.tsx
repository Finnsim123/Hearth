/**
 * Avatar — household member identity, used on person cards, timelines, inbox.
 * value: "upload:<url>" renders the photo · "preset:<hue>" renders an initial
 * on a colored disc (presets stay in the design language; no questionnaires).
 */
export const PRESET_HUES: Record<string, string> = {
  ember: "#F59E0B", indigo: "#818CF8", teal: "#34D399", blue: "#60A5FA",
  pink: "#F472B6", orange: "#FB923C", slate: "#94A3B8", violet: "#A78BFA",
};

export default function Avatar({ name, value, size = 40 }: {
  name: string; value?: string | null; size?: number;
}) {
  if (value?.startsWith("upload:")) {
    return (
      <img src={value.slice(7)} alt={name} width={size} height={size}
           style={{ borderRadius: "50%", objectFit: "cover", border: "2px solid var(--border)" }} />
    );
  }
  const hue = PRESET_HUES[value?.split(":")[1] ?? ""] ?? PRESET_HUES.ember;
  return (
    <span aria-label={name} style={{
      width: size, height: size, borderRadius: "50%", flexShrink: 0,
      background: `color-mix(in srgb, ${hue} 22%, transparent)`,
      border: `2px solid ${hue}`, color: hue,
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      fontWeight: 600, fontSize: size * 0.42,
    }}>
      {name.charAt(0).toUpperCase()}
    </span>
  );
}
