import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CoursePilot AI",
  description: "A grounded AI knowledge base for university courses",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}

