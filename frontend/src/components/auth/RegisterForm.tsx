import { zodResolver } from "@hookform/resolvers/zod";
import { Link, useNavigate } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth/useAuth";

// 백엔드 규칙과 정확히 동일: 최소 10자 + (lowercase / uppercase / digit / symbol) 중 3종 이상
const passwordSchema = z
  .string()
  .min(10, "최소 10자 이상")
  .refine(
    (v) => {
      let classes = 0;
      if (/[a-z]/.test(v)) classes++;
      if (/[A-Z]/.test(v)) classes++;
      if (/[0-9]/.test(v)) classes++;
      if (/[^a-zA-Z0-9]/.test(v)) classes++;
      return classes >= 3;
    },
    { message: "소문자·대문자·숫자·특수문자 중 3종 이상 포함" },
  );

const schema = z
  .object({
    display_name: z.string().min(1, "이름을 입력하세요"),
    organization: z.string().min(1, "조직을 입력하세요"),
    email: z.string().email("올바른 회사 이메일을 입력하세요"),
    password: passwordSchema,
    password_confirm: z.string(),
  })
  .refine((d) => d.password === d.password_confirm, {
    message: "비밀번호가 일치하지 않습니다",
    path: ["password_confirm"],
  });

type RegisterValues = z.infer<typeof schema>;

export function RegisterForm() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const form = useForm<RegisterValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      display_name: "",
      organization: "",
      email: "",
      password: "",
      password_confirm: "",
    },
  });

  const submitting = form.formState.isSubmitting;

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      await register(values);
      toast.success("가입 완료. 로그인하세요.");
      navigate({ to: "/login" });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "가입에 실패했습니다.");
    }
  });

  return (
    <Form {...form}>
      <form onSubmit={onSubmit} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <FormField
            control={form.control}
            name="display_name"
            render={({ field }) => (
              <FormItem>
                <FormLabel>이름</FormLabel>
                <FormControl>
                  <Input placeholder="박국진" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="organization"
            render={({ field }) => (
              <FormItem>
                <FormLabel>조직 (그룹/랩)</FormLabel>
                <FormControl>
                  <Input placeholder="디지털트윈AI파트" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>

        <FormField
          control={form.control}
          name="email"
          render={({ field }) => (
            <FormItem>
              <FormLabel>회사 이메일</FormLabel>
              <FormControl>
                <Input type="email" placeholder="you@samsung.com" {...field} />
              </FormControl>
              <FormDescription>회사 도메인만 허용됩니다.</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="password"
          render={({ field }) => (
            <FormItem>
              <FormLabel>비밀번호</FormLabel>
              <FormControl>
                <Input type="password" autoComplete="new-password" {...field} />
              </FormControl>
              <FormDescription>최소 10자 · 소문자·대문자·숫자·특수문자 중 3종 이상</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="password_confirm"
          render={({ field }) => (
            <FormItem>
              <FormLabel>비밀번호 확인</FormLabel>
              <FormControl>
                <Input type="password" autoComplete="new-password" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <Button type="submit" className="w-full" disabled={submitting}>
          {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          가입 신청
        </Button>

        <div className="text-center text-sm text-muted-foreground">
          이미 계정이 있나요?{" "}
          <Link to="/login" className="font-semibold text-primary hover:underline">
            로그인
          </Link>
        </div>
      </form>
    </Form>
  );
}
