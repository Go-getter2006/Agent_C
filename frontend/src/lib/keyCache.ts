/** 有界内存缓存。页面卸载即释放。 */
export class KeyCache<T> {
  private entries = new Map<string, { value: T; time: number }>();
  get(key: string) { return this.entries.get(key)?.value; }
  fresh(key: string) { return Date.now() - (this.entries.get(key)?.time ?? 0) < 10000; }
  expire(key: string) { const entry = this.entries.get(key); if (entry) entry.time = 0; }
  delete(key: string) { this.entries.delete(key); }
  put(key: string, value: T) {
    this.entries.delete(key);
    this.entries.set(key, { value, time: Date.now() });
    if (this.entries.size > 32) this.entries.delete(this.entries.keys().next().value!);
    return value;
  }
}
