// 首页的课程开关：C 语言 | 数据结构，滑块左右滑动。两门课是两台 XDOJ、两套账号，选哪门就登录哪门。
// 键盘：Tab 进来，左右方向键、Home、End 切换（单选组的标准做法）。
import { useRef, type KeyboardEvent } from "react";
import type { CourseCode } from "../types";

interface Props {
  value: CourseCode;
  options: { code: CourseCode; name: string; short: string }[];
  onChange: (code: CourseCode) => void;
}

export default function CourseSwitch({ value, options, onChange }: Props) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const index = Math.max(0, options.findIndex((o) => o.code === value));

  const onKey = (e: KeyboardEvent) => {
    let next = index;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (index + 1) % options.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (index - 1 + options.length) % options.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = options.length - 1;
    else return;
    e.preventDefault();
    onChange(options[next].code);
    refs.current[next]?.focus();
  };

  return (
    <div className="course-switch" role="radiogroup" aria-label="课程" onKeyDown={onKey}
      style={{ ["--n" as string]: options.length, ["--i" as string]: index }}>
      <span className="course-switch-thumb" aria-hidden="true" />
      {options.map((o, i) => (
        <button key={o.code} ref={(el) => { refs.current[i] = el; }} type="button" role="radio"
          aria-checked={o.code === value} tabIndex={o.code === value ? 0 : -1} title={o.name}
          onClick={() => onChange(o.code)}>
          {o.short}
        </button>
      ))}
    </div>
  );
}
