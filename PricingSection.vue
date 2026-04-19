<template>
  <section :class="$style.section" aria-labelledby="pricing-title">
    <header :class="$style.header">
      <p :class="$style.kicker">Pricing</p>
      <h2 id="pricing-title" :class="$style.title">Choose a plan that scales with your product</h2>
      <p :class="$style.subtitle">
        Start small and upgrade when your traffic grows.
      </p>

      <div :class="$style.billingToggle" role="group" aria-label="Billing period">
        <button
          type="button"
          :class="[$style.toggleButton, !isYearly && $style.toggleButtonActive]"
          :aria-pressed="String(!isYearly)"
          @click="setBilling(false)"
        >
          Monthly
        </button>
        <button
          type="button"
          :class="[$style.toggleButton, isYearly && $style.toggleButtonActive]"
          :aria-pressed="String(isYearly)"
          @click="setBilling(true)"
        >
          Yearly
          <span :class="$style.badge">Save 20%</span>
        </button>
      </div>
    </header>

    <div :class="$style.grid">
      <article
        v-for="plan in resolvedPlans"
        :key="plan.id"
        :class="[$style.card, plan.featured && $style.cardFeatured]"
        :aria-label="`${plan.name} plan`"
      >
        <p :class="$style.planName">{{ plan.name }}</p>
        <p :class="$style.priceWrap">
          <span :class="$style.price">${{ plan.currentPrice }}</span>
          <span :class="$style.period">/{{ isYearly ? 'mo (billed yearly)' : 'mo' }}</span>
        </p>
        <p v-if="isYearly" :class="$style.originalPrice">${{ plan.monthlyPrice }}/mo monthly</p>

        <ul :class="$style.featureList">
          <li v-for="feature in plan.features" :key="feature" :class="$style.featureItem">
            {{ feature }}
          </li>
        </ul>

        <button type="button" :class="[$style.cta, plan.featured && $style.ctaFeatured]">
          {{ plan.featured ? 'Start Pro' : `Choose ${plan.name}` }}
        </button>
      </article>
    </div>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue';

const isYearly = ref(false);

const plans = [
  {
    id: 'starter',
    name: 'Starter',
    monthlyPrice: 19,
    features: ['1 project', 'Email support', 'Basic analytics'],
    featured: false,
  },
  {
    id: 'pro',
    name: 'Pro',
    monthlyPrice: 49,
    features: ['10 projects', 'Priority support', 'Advanced analytics', 'A/B testing'],
    featured: true,
  },
  {
    id: 'enterprise',
    name: 'Enterprise',
    monthlyPrice: 99,
    features: ['Unlimited projects', 'SLA support', 'Custom integrations', 'Audit logs'],
    featured: false,
  },
];

const yearlyDiscountMultiplier = 0.8;

const resolvedPlans = computed(() => {
  return plans.map((plan) => {
    const yearlyPrice = Math.round(plan.monthlyPrice * yearlyDiscountMultiplier);
    return {
      ...plan,
      currentPrice: isYearly.value ? yearlyPrice : plan.monthlyPrice,
    };
  });
});

function setBilling(nextYearly) {
  isYearly.value = nextYearly;
}
</script>

<style module src="./PricingSection.module.css"></style>
