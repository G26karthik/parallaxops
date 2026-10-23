import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Shell } from "../components/Shell.tsx";
import "./globals.css";
import { Providers } from "./providers.tsx";

export const metadata: Metadata = {
  title: { default: "ParallaxOps", template: "%s · ParallaxOps" },
  description: "Distributed, quality-aware 3D reconstruction intelligence.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <Shell>{children}</Shell>
        </Providers>
      </body>
    </html>
  );
}
