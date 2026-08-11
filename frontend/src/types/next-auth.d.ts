import type { DefaultSession } from "next-auth";

declare module "next-auth" {
  interface Session extends DefaultSession {
    /** Google ID token, forwarded to FastAPI as a bearer credential. */
    idToken?: string;
    /** Unix seconds at which `idToken` expires. */
    expiresAt?: number;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    idToken?: string;
    expiresAt?: number;
  }
}
