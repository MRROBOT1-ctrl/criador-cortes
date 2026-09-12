import type { SelectHTMLAttributes } from "react";
import { cn } from "./cn";
import { FOCUS_RING, MOTION_SAFE_TRANSITION } from "./interactiveStates";

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  options: SelectOption[];
  label?: string;
  className?: string;
}

/** Usado em watermark_position, caption_style, caption_font_family,
 * profanity_filter — todos com exatamente essa mesma classe hoje. */
export function Select({ options, label, id, className, ...rest }: SelectProps) {
  const selectEl = (
    <select
      id={id}
      className={cn(
        "rounded-lg border border-border-strong bg-surface-hover px-3 py-2 text-sm text-text",
        MOTION_SAFE_TRANSITION,
        FOCUS_RING,
        "focus:border-accent",
        className,
      )}
      {...rest}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );

  if (!label) return selectEl;

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-xs font-medium text-text-muted">
        {label}
      </label>
      {selectEl}
    </div>
  );
}
