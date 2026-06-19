// 모든 fetch URL 은 상대경로로 호출한다.
// 페이지가 /apps/heax_demo_fastapi_react/ 아래에서 서빙되면 상대경로
// "api/tasks" 가 자동으로 /apps/heax_demo_fastapi_react/api/tasks 로 풀린다.
// 로컬 개발 (vite dev) 에서는 같은 origin 의 /api/tasks 로 풀린다.

export type Task = {
  id: number;
  title: string;
  done: boolean;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export const api = {
  listTasks: () => request<Task[]>("api/tasks"),
  createTask: (title: string) =>
    request<Task>("api/tasks", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),
  toggleTask: (id: number, done: boolean) =>
    request<Task>(`api/tasks/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ done }),
    }),
};
