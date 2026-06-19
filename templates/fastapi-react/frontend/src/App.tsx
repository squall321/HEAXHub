import { useEffect, useState, FormEvent } from "react";
import { api, Task } from "./api";

export default function App() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    try {
      const next = await api.listTasks();
      setTasks(next);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = title.trim();
    if (!trimmed) return;
    try {
      await api.createTask(trimmed);
      setTitle("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const onToggle = async (task: Task) => {
    try {
      await api.toggleTask(task.id, !task.done);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <main className="container">
      <header>
        <h1>My Fullstack App</h1>
        <p className="subtitle">
          TS+Vite+React 프런트엔드가 같은 origin 의 FastAPI <code>/api/tasks</code> 로
          JSON CRUD 한다. 단일 SIF 패키징, Caddy 가 서브패스 마운트.
        </p>
      </header>

      <form onSubmit={onSubmit} className="add-form">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="새 할 일 제목"
          aria-label="task title"
        />
        <button type="submit" disabled={!title.trim()}>
          추가
        </button>
      </form>

      {error && <p className="error">에러: {error}</p>}
      {loading ? (
        <p>불러오는 중...</p>
      ) : tasks.length === 0 ? (
        <p className="empty">아직 할 일이 없습니다.</p>
      ) : (
        <ul className="tasks">
          {tasks.map((t) => (
            <li key={t.id} className={t.done ? "done" : ""}>
              <label>
                <input
                  type="checkbox"
                  checked={t.done}
                  onChange={() => onToggle(t)}
                />
                <span>{t.title}</span>
              </label>
              <small>#{t.id}</small>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
