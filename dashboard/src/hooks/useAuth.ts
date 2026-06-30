import { useState, useEffect, useCallback } from "react";
import {
  CognitoUserPool,
  CognitoUser,
  AuthenticationDetails,
} from "amazon-cognito-identity-js";

let userPool: CognitoUserPool | null = null;

async function getPool() {
  if (userPool) return userPool;
  const res = await fetch("/config.json");
  const config = await res.json();
  userPool = new CognitoUserPool({
    UserPoolId: config.cognitoUserPoolId,
    ClientId: config.cognitoClientId,
  });
  return userPool;
}

export function useAuth() {
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Check for existing session on mount
  useEffect(() => {
    (async () => {
      try {
        const pool = await getPool();
        const user = pool.getCurrentUser();
        if (user) {
          user.getSession((err: any, session: any) => {
            if (!err && session?.isValid()) {
              setToken(session.getAccessToken().getJwtToken());
            }
            setLoading(false);
          });
        } else {
          setLoading(false);
        }
      } catch {
        setLoading(false);
      }
    })();
  }, []);

  const signIn = useCallback(async (username: string, password: string) => {
    setError(null);
    const pool = await getPool();
    const user = new CognitoUser({ Username: username, Pool: pool });
    const authDetails = new AuthenticationDetails({ Username: username, Password: password });

    return new Promise<void>((resolve, reject) => {
      user.authenticateUser(authDetails, {
        onSuccess: (session) => {
          const jwt = session.getAccessToken().getJwtToken();
          setToken(jwt);
          resolve();
        },
        onFailure: (err) => {
          setError(err.message || "Sign in failed");
          reject(err);
        },
        newPasswordRequired: () => {
          setError("Password change required. Contact admin.");
          reject(new Error("NEW_PASSWORD_REQUIRED"));
        },
      });
    });
  }, []);

  const signOut = useCallback(async () => {
    const pool = await getPool();
    const user = pool.getCurrentUser();
    if (user) user.signOut();
    setToken(null);
  }, []);

  return { token, loading, error, signIn, signOut, isAuthenticated: !!token };
}
