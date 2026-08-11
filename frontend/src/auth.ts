import NextAuth from "next-auth";
import Google from "next-auth/providers/google";

/**
 * Auth.js (v5) with Google.
 *
 * The FastAPI backend authenticates by verifying Google's **ID token**, so the
 * token is captured on sign-in and re-exposed on the session for the browser
 * to send as `Authorization: Bearer <id_token>`. A bearer header rather than a
 * cookie because Vercel and Render are separate origins.
 *
 * Google ID tokens last ~1 hour. `expiresAt` is surfaced so the UI can prompt
 * a re-sign-in rather than silently 401-ing.
 */
export const { handlers, signIn, signOut, auth } = NextAuth({
  providers: [Google],
  callbacks: {
    async jwt({ token, account }) {
      // `account` is only populated on the initial sign-in.
      if (account?.id_token) {
        token.idToken = account.id_token;
        token.expiresAt = account.expires_at;
      }
      return token;
    },
    async session({ session, token }) {
      session.idToken = token.idToken as string | undefined;
      session.expiresAt = token.expiresAt as number | undefined;
      return session;
    },
  },
});
