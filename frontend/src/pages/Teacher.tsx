// 教师端入口。页面在下一步补上。
import { useSession } from "../session";

export default function Teacher() {
  const { me, logout } = useSession();
  return (
    <main className="login">
      <div className="login-box">
        <h1>教师端</h1>
        <p className="note">{me?.name}，教师端的页面还在做。</p>
        <button type="button" className="btn" onClick={() => void logout()}>退出登录</button>
      </div>
    </main>
  );
}
