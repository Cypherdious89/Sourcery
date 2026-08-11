"use client";

import type { ButtonHTMLAttributes } from "react";

export type ButtonVariant = "primary" | "accent" | "secondary" | "danger" | "ghost";
export type ButtonSize = "sm" | "md";

const VARIANT: Record<ButtonVariant, string> = {
  primary:
    "bg-fg text-canvas hover:bg-fg/85 disabled:hover:bg-fg",
  accent:
    "bg-accent text-accent-fg hover:bg-accent/85 disabled:hover:bg-accent shadow-sm shadow-accent/20",
  secondary:
    "border border-line bg-surface text-fg hover:bg-inset disabled:hover:bg-surface",
  danger:
    "border border-line bg-surface text-muted hover:border-red-300 hover:bg-red-50 hover:text-red-600 dark:hover:border-red-900 dark:hover:bg-red-950/40 dark:hover:text-red-400",
  ghost: "text-muted hover:bg-inset hover:text-fg",
};

const SIZE: Record<ButtonSize, string> = {
  sm: "h-7 gap-1.5 rounded-md px-2.5 text-xs",
  md: "h-9 gap-2 rounded-lg px-3.5 text-sm",
};

/**
 * Shared button styling so every action across the app reads as one system —
 * same radius, same transition, same disabled treatment — instead of each
 * component hand-rolling its own Tailwind recipe.
 */
export function buttonClass(
  variant: ButtonVariant = "secondary",
  size: ButtonSize = "md",
  className = "",
): string {
  return `inline-flex shrink-0 cursor-pointer items-center justify-center font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${SIZE[size]} ${VARIANT[variant]} ${className}`;
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export function Button({
  variant = "secondary",
  size = "md",
  className = "",
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button type={type} className={buttonClass(variant, size, className)} {...rest} />
  );
}

/** A square icon-only button — delete/close/collapse affordances. */
export function IconButton({
  variant = "ghost",
  className = "",
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={`inline-flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${VARIANT[variant]} ${className}`}
      {...rest}
    />
  );
}
