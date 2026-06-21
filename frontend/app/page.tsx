"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { isLoggedIn } from "@/lib/auth";

export default function Root() {
  const router = useRouter();
  useEffect(() => {
    router.replace(isLoggedIn() ? "/agent" : "/signin");
  }, [router]);
  return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--bg)" }}>
      <div className="w-8 h-8 rounded-full border-2 border-t-transparent animate-spin" style={{ borderColor: "var(--accent) transparent var(--accent) var(--accent)" }} />
    </div>
  );
}
