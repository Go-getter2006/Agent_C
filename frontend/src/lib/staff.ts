export function staffRoot(role?: string): string {
  return role === "admin" ? "/developer" : "/teacher";
}
