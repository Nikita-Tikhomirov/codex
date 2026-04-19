<template>
  <section :class="$style.section" aria-labelledby="pricing-cloud-title">
    <header :class="$style.header">
      <p :class="$style.kicker">Pricing</p>
      <h2 id="pricing-cloud-title" :class="$style.title">Simple pricing for product teams</h2>
      <p :class="$style.subtitle">Pay monthly, or switch to yearly billing and save 20%.</p>

      <div :class="$style.toggle" role="group" aria-label="Billing period">
        <button
          type="button"
          :class="[$style.toggleBtn, !isYearly && $style.toggleBtnActive]"
          :aria-pressed="String(!isYearly)"
          @click="setBilling(false)"
        >
          Monthly
        </button>
        <button
          type="button"
          :class="[$style.toggleBtn, isYearly && $style.toggleBtnActive]"
          :aria-pressed="String(isYearly)"
          @click="setBilling(true)"
        >
          Yearly
          <span :class="$style.saveBadge">Save 20%</span>
        </button>
      </div>
    </header>

    <div :class="$style.grid">
      <article
        v-for="plan in viewPlans"
        :key="plan.id"
        :class="[$style.card, plan.featured && $style.featured]"
        :aria-label="`${plan.name} plan`"
      >
        <h3 :class="$style.planName">{{ plan.name }}</h3>

        <p :class="$style.priceRow">
          <span :class="$style.price">${{ plan.currentPrice }}</span>
          <span :class="$style.period">/{{ isYearly ? 'mo (billed yearly)' : 'mo' }}</span>
        </p>

        <p v-if="isYearly" :class="$style.referencePrice">${{ plan.monthlyPrice }}/mo monthly</p>

        <ul :class="$style.features">
          <li v-for="item in plan.features" :key="item" :class="$style.featureItem">{{ item }}</li>
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
const yearlyMultiplier = 0.8;

const plans = [
  {
    id: 'starter',
    name: 'Starter',
    monthlyPrice: 19,
    featured: false,
    features: ['1 project', 'Email support', 'Basic analytics'],
  },
  {
    id: 'pro',
    name: 'Pro',
    monthlyPrice: 49,
    featured: true,
    features: ['10 projects', 'Priority support', 'A/B tests', 'Advanced analytics'],
  },
  {
    id: 'enterprise',
    name: 'Enterprise',
    monthlyPrice: 99,
    featured: false,
    features: ['Unlimited projects', 'SLA support', 'Audit logs', 'Custom integrations'],
  },
];

const viewPlans = computed(() =>
  plans.map((plan) => ({
    ...plan,
    currentPrice: isYearly.value ? Math.round(plan.monthlyPrice * yearlyMultiplier) : plan.monthlyPrice,
  })),
);

function setBilling(nextValue) {
  isYearly.value = nextValue;
}
</script>

<style module src="./CloudPricingSection.module.css"></style>
