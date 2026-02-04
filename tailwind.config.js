const forms = require("@tailwindcss/forms");
const containerQueries = require("@tailwindcss/container-queries");

/** @type {import('tailwindcss').Config} */
module.exports = {
    content: [
        "./*.html",
        "./scripts/**/*.js"
    ],
    theme: {
        extend: {
            colors: {
                primary: "#3B82F6",
                "primary-hover": "#2563EB",
                accent: "#8B5CF6",
                "bg-dark": "#030712",
                "surface-dark": "#0F172A",
                "surface-elevated": "#1E293B",
                "border-dark": "#334155",
                "text-main": "#F1F5F9",
                "text-dim": "#94A3B8",
                success: "#10B981",
                warning: "#F59E0B",
                danger: "#EF4444"
            },
            fontFamily: {
                sans: ["Plus Jakarta Sans", "Inter", "sans-serif"],
                mono: ["JetBrains Mono", "monospace"]
            },
            boxShadow: {
                "glow-primary": "0 0 25px rgba(59, 130, 246, 0.25)",
                "glow-accent": "0 0 25px rgba(139, 92, 246, 0.25)"
            }
        }
    },
    plugins: [forms, containerQueries]
};
